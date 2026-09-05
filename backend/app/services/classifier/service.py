"""Main classifier service that orchestrates document classification."""

import calendar
import logging
import re
import unicodedata
from datetime import date
from typing import Dict, Any, Optional, List
from dataclasses import asdict

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete as sa_delete

from app.models.classifier import (
    ClassifierConfig, StoragePathProfile, CustomFieldMapping, ClassificationHistory,
    ClassifierOcrRetryState,
)
from app.models import LLMProvider
from app.services.paperless_client import PaperlessClient
from app.services.classifier.base_provider import (
    BaseClassifierProvider, ClassificationResult, DocumentContext,
)
from app.services.classifier.openai_provider import OpenAIToolCallingProvider
from app.services.classifier.ollama_provider import OllamaMultiCallProvider
from app.services.classifier.tool_executor import (
    ToolExecutor, effective_custom_field_document_types,
)

logger = logging.getLogger(__name__)

CLASSIFICATION_TRIGGER_TAG_NAME = "KI-klassifizieren"
FORCE_OCR_TAG_NAME = "forceocr"
OCR_FINISH_TAG_NAME = "ocrfinish"


def classification_trigger_tag_ids(
    tags: List[Dict[str, Any]], configured_tag_ids: List[int],
) -> set[int]:
    """Return configured tags that are classifier-owned processing markers.

    ``auto_classify_only_tag_ids`` is also used for prerequisite tags such as
    ``ocrfinish``. Those tags select eligible documents but belong to another
    workflow and must survive classification.
    """
    configured_ids = set(configured_tag_ids or [])
    return {
        tag["id"] for tag in (tags or [])
        if tag.get("id") in configured_ids
        and str(tag.get("name") or "").strip().casefold()
        == CLASSIFICATION_TRIGGER_TAG_NAME.casefold()
    }


def _resolve_select_custom_field_value(
    field_definition: Dict[str, Any], value: Any,
) -> Optional[Any]:
    """Translate a Paperless select label to its stable option ID."""
    if str(field_definition.get("data_type") or "").casefold() != "select":
        return value
    options = (field_definition.get("extra_data") or {}).get("select_options") or []
    candidate = str(value).strip().casefold()
    for option in options:
        option_id = str(option.get("id") or "").strip()
        option_label = str(option.get("label") or "").strip()
        if candidate in {option_id.casefold(), option_label.casefold()}:
            return option_id
    return None


def _omit_empty_custom_fields(custom_fields: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the preview/apply payload limited to fields with real values."""
    return {
        name: value
        for name, value in (custom_fields or {}).items()
        if value is not None and (not isinstance(value, str) or value.strip())
    }


def _custom_field_value_is_valid(
    mapping: CustomFieldMapping,
    value: Any,
    field_definition: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return whether a required custom-field value is genuinely usable."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return False
    normalized = str(value).strip()
    # Required reference values must not contain letters from unrelated
    # writing systems.  Punctuation, currency symbols and digits remain
    # allowed, as do all Latin letters (including German umlauts).
    if any(
        unicodedata.category(char).startswith("L")
        and "LATIN" not in unicodedata.name(char, "")
        for char in normalized
    ):
        return False
    ignored = {
        item.strip().casefold()
        for item in re.split(r"[,;\n]", mapping.ignore_values or "")
        if item.strip()
    }
    if normalized.casefold() in ignored:
        return False
    if mapping.validation_regex:
        try:
            if not re.fullmatch(mapping.validation_regex, normalized):
                return False
        except re.error:
            logger.warning(
                "Invalid validation regex for custom field '%s'",
                mapping.paperless_field_name,
            )
            return False
    if field_definition and _resolve_select_custom_field_value(field_definition, value) is None:
        return False
    return True


_TAX_AUTHORITY_RE = re.compile(
    r"\b(?:finanzamt|bundeszentralamt\s+f(?:ü|ue)r\s+steuern|"
    r"steuerverwaltung|hauptzollamt)\b",
    re.IGNORECASE,
)
_TAX_DOCUMENT_RE = re.compile(
    r"\b(?:"
    r"(?:einkommen|umsatz|lohn|gewerbe|k(?:ö|oe)rperschaft|kapitalertrag|"
    r"kirchen|grund|erbschaft|schenkung|kraftfahrzeug)steuer"
    r"(?:bescheid|erkl(?:ä|ae)rung|anmeldung|voranmeldung|festsetzung|"
    r"bescheinigung)|"
    r"steuer(?:bescheid|erkl(?:ä|ae)rung|anmeldung|festsetzung|pr(?:ü|ue)fung)|"
    r"betriebspr(?:ü|ue)fung"
    r")\b",
    re.IGNORECASE,
)


def _is_tax_document(result: ClassificationResult, doc_content: str = "") -> bool:
    """Require strong tax-document evidence; ordinary VAT wording is insufficient."""
    authority_context = "\n".join(
        part for part in [
            result.correspondent or "", result.title or "", doc_content[:2500],
        ] if part
    )
    if _TAX_AUTHORITY_RE.search(authority_context):
        return True
    subject_context = "\n".join(
        part for part in [
            result.title or "", result.summary or "", doc_content[:5000],
        ] if part
    )
    return bool(_TAX_DOCUMENT_RE.search(subject_context))


_PAYABLE_NOTICE_SUBJECT_RE = re.compile(
    r"\b(?:beitragsbescheid|geb(?:ü|ue)hrenbescheid|umlagebescheid|"
    r"jahresbeitrag|mitgliedsbeitrag|zu\s+entrichtender\s+betrag)\b",
    re.IGNORECASE,
)
_PAYMENT_DEMAND_RE = re.compile(
    r"\b(?:bitte\s+)?(?:überweisen|ueberweisen|zahlen|begleichen)\b|"
    r"\bzu\s+entrichtender\s+betrag\b",
    re.IGNORECASE,
)


def _is_payable_notice(result: ClassificationResult, doc_content: str = "") -> bool:
    """Identify contribution/fee demands whose accounting function takes priority."""
    if result.document_type != "Bescheid":
        return False
    context = "\n".join(
        part for part in [
            result.correspondent or "", result.title or "", result.summary or "",
            doc_content[:6000],
        ]
        if part
    )
    if _TAX_AUTHORITY_RE.search(context):
        return False
    return bool(
        _PAYABLE_NOTICE_SUBJECT_RE.search(context)
        and _PAYMENT_DEMAND_RE.search(context)
    )


def _add_one_calendar_month(iso_date: str) -> Optional[str]:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso_date or ""))
    if not match:
        return None
    year, month, day = map(int, match.groups())
    target_year = year + (1 if month == 12 else 0)
    target_month = 1 if month == 12 else month + 1
    target_day = min(day, calendar.monthrange(target_year, target_month)[1])
    return date(target_year, target_month, target_day).isoformat()


def _incoming_invoice_requires_payment(
    result: ClassificationResult, doc_content: str = "",
) -> bool:
    """Default incoming invoices to payable unless settlement is explicit."""
    if result.document_type != "Eingangsrechnung":
        return False

    fields = {
        str(name).strip().casefold(): value
        for name, value in (result.custom_fields or {}).items()
    }
    payment_method = str(fields.get("zahlungsart") or "").strip().casefold()
    if payment_method in {
        "lastschrift", "kartenzahlung", "barzahlung", "paypal",
    }:
        return False

    content = doc_content or ""
    context = "\n".join(
        part for part in [result.title or "", result.summary or "", content[:6000]]
        if part
    )
    if re.search(
        r"\b(?:gutschrift|stornorechnung|stornobeleg)\b",
        context, re.IGNORECASE,
    ):
        return False
    if re.search(
        r"\b(?:bereits\s+bezahlt|zahlung\s+(?:ist\s+)?eingegangen|"
        r"betrag\s+(?:wurde|ist)\s+(?:bereits\s+)?bezahlt|"
        r"bezahlt\s+(?:mit|per)|bar\s+bezahlt|betrag\s+dankend\s+erhalten)\b",
        context, re.IGNORECASE,
    ):
        return False
    if re.search(
        r"\b(?:wird|werden)\b[^\n.]{0,100}\b(?:abgebucht|eingezogen)\b|"
        r"\b(?:sepa[- ]?lastschrift|bankeinzug)\b",
        context, re.IGNORECASE,
    ):
        return False
    if re.search(
        r"\b(?:bitte\s+)?(?:nicht|keinesfalls)\s+"
        r"(?:überweisen|ueberweisen|zahlen|begleichen)\b",
        context, re.IGNORECASE,
    ):
        return False
    if re.search(
        r"\b(?:zu\s+zahlen|zu\s+entrichtender\s+betrag|offener\s+betrag|"
        r"restbetrag|gesamt)\s*[:\-]?\s*(?:€|eur)?\s*0[,.]00\b",
        context, re.IGNORECASE,
    ):
        return False
    return True

# Legal-form stripping (leaf module) + the opt-in correspondent matcher (pure module)
# live in their own files to keep the import graph acyclic — see correspondent_normalize.py.
from app.services.classifier.correspondent_normalize import _strip_legal_forms
from app.services.classifier.correspondent_matcher import match_correspondent


# Reference indicators that legitimise a YYYY-NNNNN number in a title.
_TITLE_REF_INDICATORS = re.compile(
    r"\b(?:Nr|Re|Ref|Rechnung|Auftrag|Aktenzeichen|Vertrags?|AZ|Az)\s*[-.:]\s*$",
    re.IGNORECASE,
)
# Matches "YYYY-NNNNN" style numbers (year-personalnumber) in titles.
_TITLE_YEAR_ID_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{4,6})\b")

_TRANSACTION_TITLE_TYPES = {
    "Eingangsrechnung", "Ausgangsrechnung", "Zahlungsbeleg",
}

_RECEIPT_MARKER_RE = re.compile(
    r"\b(?:kassenbon|bonduplikat|kundenbeleg|quittung|karten(?:zahlung|beleg)|barzahlung)\b",
    re.IGNORECASE,
)
_RECEIPT_REFERENCE_PATTERNS = (
    re.compile(
        r"\b(?:beleg|bon)[ \t]*(?:[-./][ \t]*)?(?:nr\.?|nummer)"
        r"[ \t]*[:#-]?[ \t]*[^A-Z0-9\n]{0,6}([A-Z0-9][A-Z0-9./_-]{1,39})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:tse[- ]?)?(?:transaktions?|vorgangs?)[ \t]*(?:[-./][ \t]*)?"
        r"(?:nr\.?|nummer)[ \t]*[:#-]?[ \t]*[^A-Z0-9\n]{0,6}"
        r"([A-Z0-9][A-Z0-9./_-]{1,39})",
        re.IGNORECASE,
    ),
)


def _extract_receipt_reference(content: str) -> Optional[str]:
    """Return the printed receipt ID, preferring Beleg-/Bon-Nr.

    This is intentionally limited to text that clearly identifies a receipt.
    Register, terminal, approval and operator identifiers are not accepted.
    A transaction/TSE reference is only the fallback when no explicit receipt
    number is printed.
    """
    if not content or not _RECEIPT_MARKER_RE.search(content):
        return None

    for pattern in _RECEIPT_REFERENCE_PATTERNS:
        match = pattern.search(content)
        if match:
            return match.group(1).strip(" .,:;#{}[]()")
    return None


def _add_preview_status_tags(result: ClassificationResult, config: ClassifierConfig) -> None:
    """Expose deterministic apply-time status tags in the review result."""
    chosen_type = str(result.document_type or "").strip().casefold()
    if not chosen_type:
        return

    existing_names = {str(tag).strip().casefold() for tag in (result.existing_tags or [])}
    result_names = {str(tag).strip().casefold() for tag in (result.tags or [])}
    for rule in (getattr(config, "status_tag_rules", None) or []):
        if not isinstance(rule, dict) or not rule.get("enabled", True):
            continue
        tag_name = str(rule.get("tag_name") or "").strip()
        allowed_types = {
            str(name).strip().casefold()
            for name in (rule.get("document_types") or [])
            if str(name).strip()
        }
        blocker_name = str(rule.get("skip_if_tag_name") or "").strip().casefold()
        if (
            not tag_name
            or chosen_type not in allowed_types
            or (blocker_name and blocker_name in existing_names)
        ):
            continue
        if tag_name.casefold() not in result_names:
            result.tags.append(tag_name)
            result_names.add(tag_name.casefold())


def _clean_title(title: str, created_date: str = None) -> str:
    """Minimal safety net: remove obvious personal-number patterns from titles.

    Only removes "YYYY-NNNNN" patterns that are NOT preceded by a reference
    keyword (Nr., Re-, Rechnung, ...). Everything else is left to the LLM.
    """
    if not title:
        return title

    def _replace_fake_ref(m: re.Match) -> str:
        before = title[: m.start()].rstrip()
        if _TITLE_REF_INDICATORS.search(before):
            return m.group(0)  # keep — it's a real reference number
        return ""  # remove the entire YYYY-NNNNN pattern

    cleaned = _TITLE_YEAR_ID_RE.sub(_replace_fake_ref, title)
    cleaned = re.sub(r"  +", " ", cleaned).strip(" -,.")
    return cleaned


def _format_transaction_title(result: ClassificationResult) -> Optional[str]:
    """Build the practice's canonical accounting-document title.

    Missing values remain visible as explicit placeholders. This keeps the title
    structurally stable and makes incomplete extraction obvious in the review queue.
    """
    if result.document_type not in _TRANSACTION_TITLE_TYPES:
        return None

    fields = result.custom_fields or {}
    # The canonical title depends on the custom-field extraction pipeline.  When
    # that pipeline is disabled, keep the title produced by the configured title
    # prompt instead of replacing it with an unavoidable placeholder.
    if "Rechnungsnummer" not in fields:
        return None

    raw_reference = fields.get("Rechnungsnummer")
    if raw_reference in (None, ""):
        receipt_context = "\n".join(
            part for part in [result.title or "", result.summary or ""] if part
        )
        if _RECEIPT_MARKER_RE.search(receipt_context):
            interim_title = str(result.title or "").strip()
            if interim_title and not re.search(
                r"\bohne\s+rechnungsnummer\b", interim_title, re.IGNORECASE,
            ):
                return interim_title
            receipt_label = (
                "Bonduplikat" if re.search(
                    r"\bbonduplikat\b", receipt_context, re.IGNORECASE,
                ) else "Kassenbon"
            )
            date_part = f" {result.created_date}" if result.created_date else ""
            corr_part = f" - {result.correspondent}" if result.correspondent else ""
            return f"{receipt_label}{date_part}{corr_part}"
        reference = "ohne Rechnungsnummer"
    else:
        reference = str(raw_reference).strip()
    correspondent = str(result.correspondent or "Unbekannter Korrespondent").strip()
    reminder_level = fields.get("Mahnstufe")
    reminder_part = f" ({str(reminder_level).strip()})" if reminder_level not in (None, "") else ""
    return f"{reference}{reminder_part} - {correspondent}"


class DocumentClassifierService:
    """Orchestrates document classification using the configured provider."""

    def __init__(self, db: AsyncSession, paperless: PaperlessClient):
        self.db = db
        self.paperless = paperless

    async def get_config(self) -> ClassifierConfig:
        result = await self.db.execute(
            select(ClassifierConfig).where(ClassifierConfig.id == 1)
        )
        config = result.scalar_one_or_none()
        if not config:
            config = ClassifierConfig(id=1)
            self.db.add(config)
            await self.db.commit()
            await self.db.refresh(config)
        return config

    async def save_config(self, data: Dict[str, Any]) -> ClassifierConfig:
        config = await self.get_config()
        for key, value in data.items():
            if hasattr(config, key) and key not in ("id", "created_at", "updated_at"):
                setattr(config, key, value)
        await self.db.commit()
        await self.db.refresh(config)
        return config

    async def get_storage_profiles(self) -> List[StoragePathProfile]:
        result = await self.db.execute(
            select(StoragePathProfile).order_by(StoragePathProfile.person_name)
        )
        return list(result.scalars().all())

    async def get_active_storage_profiles(self) -> List[StoragePathProfile]:
        """Return profiles for the storage paths of the connected Paperless instance.

        Profile rows can outlive a Paperless connection change. Paperless IDs are only
        unique within one instance, so an old profile must not be reused when the same
        numeric ID now belongs to a differently named path. Unsaved current paths are
        enabled with neutral defaults, matching the settings UI.
        """
        saved_profiles = await self.get_storage_profiles()
        saved_by_id = {profile.paperless_path_id: profile for profile in saved_profiles}
        try:
            current_paths = await self.paperless.get_storage_paths(use_cache=False)
        except Exception:
            logger.exception(
                "Could not refresh Paperless storage paths; using saved profiles"
            )
            return saved_profiles

        active_profiles: List[StoragePathProfile] = []
        for path in current_paths:
            path_id = path.get("id")
            if path_id is None:
                continue
            current_name = str(path.get("name") or "").strip()
            saved = saved_by_id.get(path_id)
            if (
                saved is not None
                and str(saved.paperless_path_name or "").strip().casefold()
                == current_name.casefold()
            ):
                active_profiles.append(saved)
                continue

            if saved is not None:
                logger.warning(
                    "Ignoring stale storage profile for Paperless path id %s: %r -> %r",
                    path_id, saved.paperless_path_name, current_name,
                )
            active_profiles.append(StoragePathProfile(
                paperless_path_id=path_id,
                paperless_path_name=current_name,
                paperless_path_path=str(path.get("path") or ""),
                enabled=True,
                person_name="",
                path_type="private",
                context_prompt="",
            ))
        return active_profiles

    async def save_storage_profile(self, data: Dict[str, Any]) -> StoragePathProfile:
        path_id = data.get("paperless_path_id")
        result = await self.db.execute(
            select(StoragePathProfile).where(
                StoragePathProfile.paperless_path_id == path_id
            )
        )
        profile = result.scalar_one_or_none()
        if not profile:
            profile = StoragePathProfile(paperless_path_id=path_id)
            self.db.add(profile)

        for key, value in data.items():
            if hasattr(profile, key) and key not in ("id", "created_at", "updated_at"):
                setattr(profile, key, value)

        await self.db.commit()
        await self.db.refresh(profile)
        return profile

    async def get_custom_field_mappings(self) -> List[CustomFieldMapping]:
        result = await self.db.execute(
            select(CustomFieldMapping).order_by(CustomFieldMapping.paperless_field_name)
        )
        return list(result.scalars().all())

    async def save_custom_field_mapping(self, data: Dict[str, Any]) -> CustomFieldMapping:
        field_id = data.get("paperless_field_id")
        result = await self.db.execute(
            select(CustomFieldMapping).where(
                CustomFieldMapping.paperless_field_id == field_id
            )
        )
        mapping = result.scalar_one_or_none()
        if not mapping:
            mapping = CustomFieldMapping(paperless_field_id=field_id)
            self.db.add(mapping)

        data = dict(data)
        data["applicable_document_types"] = effective_custom_field_document_types(
            data.get("paperless_field_name") or getattr(mapping, "paperless_field_name", ""),
            data.get("applicable_document_types"),
        )
        applicable = set(data["applicable_document_types"])
        data["required_document_types"] = [
            name for name in (data.get("required_document_types") or [])
            if name in applicable
        ]

        for key, value in data.items():
            if hasattr(mapping, key) and key not in ("id", "created_at", "updated_at"):
                setattr(mapping, key, value)

        await self.db.commit()
        await self.db.refresh(mapping)
        return mapping

    def _build_tool_executor(
        self, config: ClassifierConfig,
        storage_profiles: list, field_mappings: list,
    ) -> ToolExecutor:
        return ToolExecutor(
            paperless=self.paperless,
            storage_profiles=storage_profiles,
            custom_field_mappings=field_mappings,
            excluded_tag_ids=config.excluded_tag_ids or [],
            excluded_correspondent_ids=config.excluded_correspondent_ids or [],
            excluded_document_type_ids=config.excluded_document_type_ids or [],
            tags_ignore=config.tags_ignore or [],
        )

    async def _get_llm_provider(self, provider_name: str) -> 'LLMProvider':
        """Get a configured LLMProvider from the central table."""
        result = await self.db.execute(
            select(LLMProvider).where(LLMProvider.name == provider_name)
        )
        provider = result.scalar_one_or_none()
        if not provider:
            raise ValueError(f"Provider '{provider_name}' not found in LLM settings.")
        return provider

    async def _get_classifier_provider_name(self) -> str:
        """Get the classifier provider name from AppSettings."""
        from app.models import AppSettings
        result = await self.db.execute(select(AppSettings).where(AppSettings.id == 1))
        app_settings = result.scalar_one_or_none()
        if app_settings and getattr(app_settings, "classifier_provider", None):
            return app_settings.classifier_provider
        return "ollama"

    async def _build_provider(self, config: ClassifierConfig) -> BaseClassifierProvider:
        """Build the appropriate provider based on central LLM settings."""
        storage_profiles = await self.get_active_storage_profiles()
        field_mappings = await self.get_custom_field_mappings()
        tool_executor = self._build_tool_executor(config, storage_profiles, field_mappings)

        provider_name = await self._get_classifier_provider_name()
        return await self._create_provider_instance(provider_name, tool_executor)

    async def _create_provider_instance(
        self, provider_name: str, tool_executor: ToolExecutor,
        model_override: Optional[str] = None,
    ) -> BaseClassifierProvider:
        """Create a provider instance from the central LLMProvider table."""
        llm = await self._get_llm_provider(provider_name)
        model = model_override or llm.classifier_model or llm.model

        if provider_name == "openai":
            if not llm.api_key:
                raise ValueError("OpenAI API key not configured. Set it in Settings → LLM.")
            return OpenAIToolCallingProvider(
                api_key=llm.api_key, model=model, tool_executor=tool_executor,
            )
        elif provider_name == "ollama":
            host = llm.api_base_url or "http://localhost:11434"
            return OllamaMultiCallProvider(
                host=host, model=model, tool_executor=tool_executor,
            )
        else:
            raise ValueError(
                f"Unsupported provider '{provider_name}'. Use Ollama or OpenAI."
            )

    async def _get_active_classifier_provider_name(self) -> str:
        """Alias for backward compat."""
        return await self._get_classifier_provider_name()

    def _build_config_dict(self, config: ClassifierConfig) -> Dict[str, Any]:
        return {
            "enable_title": config.enable_title,
            "enable_tags": config.enable_tags,
            "enable_correspondent": config.enable_correspondent,
            "enable_document_type": config.enable_document_type,
            "enable_storage_path": config.enable_storage_path,
            "enable_created_date": config.enable_created_date,
            "enable_custom_fields": config.enable_custom_fields,
            "tag_behavior": config.tag_behavior,
            "tags_min": config.tags_min if config.tags_min is not None else 0,
            "tags_max": config.tags_max or 5,
            "correspondent_behavior": config.correspondent_behavior,
            "prompt_title": config.prompt_title or "",
            "prompt_tags": config.prompt_tags or "",
            "prompt_correspondent": config.prompt_correspondent or "",
            "prompt_document_type": config.prompt_document_type or "",
            "prompt_date": config.prompt_date or "",
            "system_prompt": config.system_prompt,
            "tags_ignore": config.tags_ignore or [],
            "storage_path_behavior": getattr(config, "storage_path_behavior", "always") or "always",
            "storage_path_override_names": getattr(config, "storage_path_override_names", ["Zuweisen"]) or ["Zuweisen"],
            "correspondent_trim_prompt": bool(getattr(config, "correspondent_trim_prompt", False)),
            "correspondent_strip_legal": bool(getattr(config, "correspondent_strip_legal", False)),
        }

    async def _build_document_context(self, document_id: int) -> tuple:
        """Build DocumentContext + raw doc_data from Paperless. Returns (context, doc_data) or raises."""
        doc_data = await self.paperless.get_document(document_id)
        if not doc_data:
            return None, None

        all_tags = await self.paperless.get_tags(use_cache=True)
        tag_map = {t["id"]: t["name"] for t in all_tags}
        current_tag_names = [tag_map.get(tid, str(tid)) for tid in doc_data.get("tags", [])]

        all_correspondents = await self.paperless.get_correspondents(use_cache=True)
        corr_map = {c["id"]: c["name"] for c in all_correspondents}
        current_corr = corr_map.get(doc_data.get("correspondent"), None)

        all_types = await self.paperless.get_document_types(use_cache=True)
        type_map = {dt["id"]: dt["name"] for dt in all_types}
        current_type = type_map.get(doc_data.get("document_type"), None)

        all_paths = await self.paperless.get_storage_paths(use_cache=True)
        path_map = {p["id"]: p["name"] for p in all_paths}
        current_path_id = doc_data.get("storage_path")
        current_path_name = path_map.get(current_path_id) if current_path_id else None

        document = DocumentContext(
            document_id=document_id,
            current_title=doc_data.get("title", ""),
            content=doc_data.get("content", ""),
            current_tags=current_tag_names,
            current_correspondent=current_corr,
            current_document_type=current_type,
            current_storage_path=current_path_name,
            created_date=doc_data.get("created"),
        )
        return document, doc_data

    def _normalize_date(self, date_str: str) -> Optional[str]:
        """Normalize various date formats to YYYY-MM-DD for comparison."""
        if not date_str:
            return None
        date_str = date_str.strip()
        # Already ISO: 1987-06-17
        if re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return date_str
        # German DD.MM.YYYY
        m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", date_str)
        if m:
            return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
        # German DD.MM.YY
        m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{2})$", date_str)
        if m:
            year = int(m.group(3))
            year = 2000 + year if year < 50 else 1900 + year
            return f"{year}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
        return None

    def _normalize_custom_fields(self, custom_fields: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize custom field values to consistent formats regardless of LLM output."""
        if not custom_fields:
            return custom_fields

        normalized = {}
        for key, value in custom_fields.items():
            if value is None:
                normalized[key] = None
                continue

            val = str(value).strip()

            if not val or val.lower() in ("null", "none", "n/a", "-", "--", "nicht gefunden", "nicht vorhanden"):
                normalized[key] = None
                continue

            key_lower = key.lower()

            if "mahnstufe" in key_lower and val.casefold() in (
                "0", "keine", "nein", "nicht zutreffend", "nicht vorhanden"
            ):
                normalized[key] = None
                continue

            if any(k in key_lower for k in ("iban", "kontonummer", "konto")):
                cleaned = re.sub(r'[\s\-\.]+', '', val)
                if re.match(r'^[A-Z]{2}\d', cleaned, re.IGNORECASE):
                    val = cleaned.upper()
                elif re.sub(r'\D', '', cleaned):
                    val = cleaned
                else:
                    normalized[key] = None
                    continue

            elif any(k in key_lower for k in ("betrag", "summe", "gesamt", "preis", "kosten")):
                val = re.sub(r'[€$\s]', '', val)
                val = val.replace('\u00a0', '')
                if re.match(r'^\d{1,3}(\.\d{3})+(,\d{1,2})?$', val):
                    val = val.replace('.', '').replace(',', '.')
                elif re.match(r'^\d{1,3}(\.\d{3})+$', val):
                    val = val.replace('.', '')
                elif ',' in val and '.' not in val:
                    val = val.replace(',', '.')
                try:
                    normalized[key] = round(float(val), 2)
                    continue
                except ValueError:
                    pass

            normalized[key] = val

        return normalized

    def _enforce_incoming_invoice_fields(
        self,
        result: ClassificationResult,
        doc_content: str,
        source_created_date: Optional[str] = None,
    ) -> None:
        """Apply high-confidence accounting rules the local LLM must not guess.

        Small local models repeatedly interpreted wording such as "zwei Tage nach
        Fälligkeit wird abgebucht" as a two-day payment term.  That wording only
        announces the collection date.  For an incoming invoice without a real
        due date or payment term, immediate maturity means the invoice date is
        the due date.

        The same document language gives deterministic evidence for direct debit:
        an announced debit together with a mandate reference or creditor ID is a
        Lastschrift even when the literal word "Lastschrift" is absent.
        """
        if result.document_type != "Eingangsrechnung" or not result.custom_fields:
            return

        fields = result.custom_fields
        keys_by_name = {str(key).strip().casefold(): key for key in fields}
        content = doc_content or ""
        content_lower = content.casefold()

        bank_key = keys_by_name.get("bank")
        if bank_key is not None and fields.get(bank_key):
            proposed_bank = str(fields[bank_key]).strip()
            normalized_bank = re.sub(r"[^a-z0-9]", "", proposed_bank.casefold())
            normalized_content = re.sub(r"[^a-z0-9]", "", content_lower)
            if normalized_bank and normalized_bank not in normalized_content:
                fields[bank_key] = None
                result.debug_info["unsupported_bank_removed"] = proposed_bank
                logger.info(
                    "Incoming-invoice bank rule: removed unsupported value %r",
                    proposed_bank,
                )

        due_key = keys_by_name.get("fälligkeitsdatum")
        if due_key is not None:
            # A new deadline in a reminder takes precedence over the original
            # invoice due date shown in a reference table.
            requested_payment_deadline = re.search(
                r"(?:bitte\s+)?(?:überweisen|zahlen|begleichen)"
                r"[^\n.]{0,100}?\bbis\s+(\d{1,2}\.\d{1,2}\.\d{2,4})\b",
                content_lower,
            )
            explicit_due_date = re.search(
                r"(?:fälligkeitsdatum|fälligkeitstag|fälligkeit|zahlbar\s+bis|"
                r"zahlungsziel|zahlungstermin)[^\n.]{0,60}?"
                r"\b(\d{1,2}\.\d{1,2}\.\d{2,4})\b",
                content_lower,
            )
            explicit_payment_term = re.search(
                r"(?:zahlbar|fällig|zahlungsziel)\s*[:\-]?\s*"
                r"(?:innerhalb|binnen)?\s*(?:"
                r"\d+\s*(?:tage|tagen|wochen|monate|monaten)|"
                r"(?:eines?|einem)\s+monats?"
                r")\b|\binnerhalb\s+(?:eines?|einem)\s+monats?\b",
                content_lower,
            )

            deadline_match = requested_payment_deadline or explicit_due_date
            if deadline_match:
                deadline = self._normalize_date(deadline_match.group(1))
                if deadline:
                    previous = fields.get(due_key)
                    fields[due_key] = deadline
                    if previous != deadline:
                        logger.info(
                            "Incoming-invoice due-date rule: '%s' -> '%s' "
                            "(explicit payment deadline)",
                            previous,
                            deadline,
                        )
            elif not explicit_payment_term:
                invoice_date = self._normalize_date(result.created_date or "")
                if not invoice_date and source_created_date:
                    source_date = str(source_created_date).strip()
                    invoice_date = self._normalize_date(source_date)
                    if not invoice_date and re.match(r"^\d{4}-\d{2}-\d{2}", source_date):
                        invoice_date = source_date[:10]
                if invoice_date:
                    previous = fields.get(due_key)
                    fields[due_key] = invoice_date
                    if previous != invoice_date:
                        logger.info(
                            "Incoming-invoice due-date rule: '%s' -> '%s' "
                            "(no explicit due date or payment term)",
                            previous,
                            invoice_date,
                        )

        payment_key = keys_by_name.get("zahlungsart")
        if payment_key is not None:
            explicit_direct_debit = bool(
                re.search(r"\b(?:lastschrift|sepa[- ]?lastschrift|bankeinzug)\b", content_lower)
            )
            announced_mandate_debit = bool(
                re.search(r"\b(?:abgebucht|abbuchung|eingezogen)\b", content_lower)
                and re.search(
                    r"\b(?:mandatsreferenz(?:nummer)?|gläubiger[- ]?identifikationsnummer)\b",
                    content_lower,
                )
            )
            if explicit_direct_debit or announced_mandate_debit:
                previous = fields.get(payment_key)
                fields[payment_key] = "Lastschrift"
                if previous != "Lastschrift":
                    logger.info(
                        "Incoming-invoice payment rule: '%s' -> 'Lastschrift' "
                        "(explicit debit evidence)",
                        previous,
                    )
            else:
                credit_or_refund = bool(re.search(
                    r"\b(?:gutschrift|rechnungskorrektur|stornorechnung|stornobeleg|"
                    r"zurückerstattet|zurueckerstattet|erstattung)\b",
                    content_lower,
                ))
                explicit_other_method = bool(re.search(
                    r"\b(?:karten(?:zahlung)?|kreditkarte|visa|mastercard|paypal|"
                    r"bar(?:zahlung)?|gutschein|gift[ -]?card|amazon[ -]?guthaben|"
                    r"verrechnet|verrechnung)\b",
                    content_lower,
                ))
                if credit_or_refund and not explicit_other_method:
                    previous = fields.get(payment_key)
                    fields[payment_key] = "Überweisung"
                    result.debug_info["payment_method_correction"] = (
                        "Gutschrift/Rechnungskorrektur ohne abweichende Zahlungsinformation"
                    )
                    logger.info(
                        "Incoming-invoice payment rule: %r -> 'Überweisung' "
                        "(credit/refund without another payment method)",
                        previous,
                    )

    async def _align_custom_fields_with_document_type(
        self, result: ClassificationResult, config: ClassifierConfig,
    ) -> None:
        """Keep and expose only configured fields applicable to the final type."""
        if not getattr(config, "enable_custom_fields", False) or not result.document_type:
            return
        selected_type = result.document_type.casefold()
        mappings = await self.get_custom_field_mappings()
        applicable_names = {
            mapping.paperless_field_name
            for mapping in mappings
            if mapping.enabled and selected_type in {
                name.casefold() for name in effective_custom_field_document_types(
                    mapping.paperless_field_name,
                    mapping.applicable_document_types,
                )
            }
        }
        current_by_lower = {
            str(name).strip().casefold(): value
            for name, value in (result.custom_fields or {}).items()
        }
        result.custom_fields = {
            name: current_by_lower.get(name.casefold())
            for name in applicable_names
        }

    def _populate_payable_notice_fields(
        self, result: ClassificationResult, doc_content: str,
    ) -> None:
        """Extract high-confidence invoice fields from a payable contribution notice."""
        fields = result.custom_fields or {}
        keys = {str(name).strip().casefold(): name for name in fields}
        content = doc_content or ""

        amount_key = keys.get("betrag")
        amount_match = re.search(
            r"zu\s+entrichtender\s+betrag\s+([\d.]+(?:,\d{1,2})?)\s*(?:€|eur)",
            content, re.IGNORECASE,
        )
        if amount_key is not None and amount_match:
            fields[amount_key] = amount_match.group(1)

        reference_key = keys.get("rechnungsnummer")
        member_match = re.search(
            r"\bmitgliedsnummer\s*[:#-]?\s*([A-Z0-9][A-Z0-9./_-]{2,39})",
            content, re.IGNORECASE,
        )
        year_match = re.search(r"\bbeitragsbescheid\s+((?:19|20)\d{2})\b", content, re.IGNORECASE)
        if reference_key is not None and member_match:
            member = member_match.group(1).strip(" .,:;#")
            fields[reference_key] = (
                f"{member}-{year_match.group(1)}" if year_match else member
            )

        due_key = keys.get("fälligkeitsdatum")
        if (
            due_key is not None
            and re.search(r"\binnerhalb\s+eines\s+monats\b", content, re.IGNORECASE)
        ):
            due_date = _add_one_calendar_month(result.created_date or "")
            if due_date:
                fields[due_key] = due_date

        payment_key = keys.get("zahlungsart")
        if payment_key is not None and re.search(
            r"\b(?:bitte\s+)?(?:überweisen|ueberweisen)\b", content, re.IGNORECASE,
        ):
            fields[payment_key] = "Überweisung"

        result.custom_fields = fields

    def _build_protected_matchers(self, config) -> list:
        """Build regex matchers from tags_protected patterns."""
        matchers = []
        for pat in (config.tags_protected or []):
            if "*" in pat:
                regex_pat = re.escape(pat).replace(r"\*", ".*")
                matchers.append(("regex", re.compile(f"^{regex_pat}$", re.IGNORECASE)))
            else:
                matchers.append(("exact", pat.lower()))
        return matchers

    def _is_tag_protected(self, tag_name: str, matchers: list) -> bool:
        """Check if a tag matches any protected pattern."""
        for kind, matcher in matchers:
            if kind == "exact" and tag_name.lower() == matcher:
                return True
            elif kind == "regex" and matcher.match(tag_name):
                return True
        return False

    def _deduplicate_tags(self, tags: List[str]) -> List[str]:
        """Remove redundant tags where one is a substring of another."""
        if len(tags) <= 1:
            return tags
        result = []
        sorted_tags = sorted(tags, key=len)
        for i, tag in enumerate(sorted_tags):
            is_redundant = False
            for j, other in enumerate(sorted_tags):
                if i == j:
                    continue
                if len(tag) < len(other) and tag.lower() in other.lower():
                    is_redundant = True
                    break
                if tag.lower() == other.lower() and i < j:
                    is_redundant = True
                    break
            if not is_redundant:
                result.append(tag)
        return result

    def _build_context_words(self, result: ClassificationResult, doc_content: str = "") -> set:
        """Build a set of context words from all available document info."""
        words = set()
        for source in [result.title, result.correspondent, result.summary]:
            if source:
                words.update(w.lower() for w in re.split(r'[\s,.\-/]+', source) if len(w) > 3)
        if result.document_type:
            words.add(result.document_type.lower())
        if doc_content:
            snippet = doc_content[:3000].lower()
            words.update(w for w in re.split(r'[\s,.\-/]+', snippet) if len(w) > 4)
        return words

    def _matches_ignore_patterns(self, tag: str, config: 'ClassifierConfig') -> bool:
        """Check if a tag matches any configured ignore pattern (exact or wildcard)."""
        tag_lower = tag.lower().strip()
        for pat in (config.tags_ignore or []):
            if "*" in pat:
                regex_pat = re.escape(pat).replace(r"\*", ".*")
                if re.match(f"^{regex_pat}$", tag, re.IGNORECASE):
                    return True
            elif pat.lower() == tag_lower:
                return True
        return False

    def _verify_result_coherence(
        self, result: ClassificationResult, config: ClassifierConfig,
        doc_content: str = "",
    ):
        """Verify result coherence. Removes system tags, enforces tag limits,
        and uses relevance scoring to decide which tags to keep."""
        tags_max = config.tags_max or 5

        issues = []
        if config.enable_document_type and not result.document_type:
            issues.append("document_type is empty")
        if config.enable_correspondent and not result.correspondent:
            issues.append("correspondent is empty")
        if config.enable_title and not result.title:
            issues.append("title is empty")
        if issues:
            logger.warning(f"Verification: missing fields: {', '.join(issues)}")

        # Structural tag rules are more reliable than keyword relevance for documents
        # containing many unrelated line items (especially bank statements).
        if result.document_type in {"Kontoauszug", "Kreditkartenabrechnung"}:
            result.tags = ["Bank"]
        elif result.document_type == "Ausgangsrechnung":
            result.tags = []

        if (
            any(str(tag).strip().casefold() == "steuern" for tag in (result.tags or []))
            and not _is_tax_document(result, doc_content)
        ):
            result.tags = [
                tag for tag in result.tags
                if str(tag).strip().casefold() != "steuern"
            ]
            result.debug_info["tax_tag_removed"] = (
                "Kein Finanzamt und kein eindeutiger steuerlicher Hauptgegenstand"
            )
            logger.info(
                "Verification: removed tag 'Steuern'; only ordinary tax wording found"
            )

        if result.tags:
            before_count = len(result.tags)
            result.tags = [t for t in result.tags if not self._matches_ignore_patterns(t, config)]
            removed_ignore = before_count - len(result.tags)
            if removed_ignore:
                logger.info(f"Verification: removed {removed_ignore} tags via ignore patterns")

            result.tags = self._deduplicate_tags(result.tags)
            tags_min = config.tags_min if config.tags_min is not None else 0
            context_words = self._build_context_words(result, doc_content)

            def _tag_score(tag: str) -> int:
                tag_lower = tag.lower()
                tag_words = set(re.split(r'[\s\-/]+', tag_lower))
                score = 0
                for tw in tag_words:
                    if len(tw) < 3:
                        continue
                    for cw in context_words:
                        if tw in cw or cw in tw:
                            score += 2
                            break
                if result.title and tag_lower in result.title.lower():
                    score += 3
                if result.summary and tag_lower in result.summary.lower():
                    score += 3
                if doc_content and tag_lower in doc_content[:4000].lower():
                    score += 5
                return score

            # Remove tags with zero relevance to document content (if enough tags remain)
            if doc_content or result.title or result.summary:
                scored = [(t, _tag_score(t)) for t in result.tags]
                relevant = [(t, s) for t, s in scored if s > 0]
                irrelevant = [t for t, s in scored if s == 0]
                if irrelevant and len(relevant) >= tags_min:
                    logger.info(f"Verification: removed {len(irrelevant)} irrelevant tags (score=0): {irrelevant}")
                    result.tags = [t for t, _ in relevant]

            # Score and trim if still over limit
            if len(result.tags) > tags_max:
                scored_tags = [(t, _tag_score(t)) for t in result.tags]
                scored_tags.sort(key=lambda x: x[1], reverse=True)
                removed = [t for t, _ in scored_tags[tags_max:]]
                logger.info(f"Trimming {len(result.tags)} tags to max {tags_max}, removed: {removed}")
                result.tags = [t for t, _ in scored_tags[:tags_max]]

        # Privacy and administrative workflow tags are policy, not a fuzzy language
        # decision. Add them after relevance scoring so a tag such as "Vertraulich"
        # is not removed merely because that literal word is absent from the document.
        required_tags = []
        if result.document_type == "Personalunterlage" or result.storage_path_name == "Personal":
            required_tags.extend(["Personal", "Vertraulich"])
        if result.document_type == "Bescheid":
            required_tags.append("Behörden")
        if result.document_type in {"Eingangsrechnung", "Ausgangsrechnung"}:
            # Mahnung and Storno are accounting-document forms, not document types.
            # Restrict detection to title/summary and the document head so mentions in
            # line items or long attachments do not create a workflow tag.
            subtype_context = "\n".join(
                part for part in [result.title or "", result.summary or "", doc_content[:1500]] if part
            )
            if re.search(r"\b(?:mahnung|zahlungserinnerung)\b", subtype_context, re.IGNORECASE):
                required_tags.append("Mahnung")
            if re.search(r"\b(?:stornorechnung|storno(?:beleg)?)\b", subtype_context, re.IGNORECASE):
                required_tags.append("Storno")
        if result.document_type == "Eingangsrechnung":
            # Missing payment details are not evidence of settlement. Default to
            # the safe workflow and suppress it only with explicit counterevidence.
            if _incoming_invoice_requires_payment(result, doc_content):
                required_tags.append("Bezahlen")
        for required_tag in required_tags:
            if required_tag not in result.tags:
                result.tags.append(required_tag)
        result.tags = self._deduplicate_tags(result.tags)

        logger.info(f"Verification complete: tags={result.tags}, doc_type={result.document_type}, "
                     f"corr={result.correspondent}, sp={result.storage_path_id}")

    async def _project_processing_tags(
        self, result: ClassificationResult, config: ClassifierConfig,
        doc_content: str = "",
    ) -> None:
        """Project apply-time workflow tag additions and removals into review."""
        all_tags = await self.paperless.get_tags(use_cache=True)
        names_by_id = {tag["id"]: tag["name"] for tag in all_tags}

        removable_trigger_ids = classification_trigger_tag_ids(
            all_tags, getattr(config, "auto_classify_only_tag_ids", None) or [],
        )
        removed_names = {
            names_by_id[tag_id].casefold()
            for tag_id in removable_trigger_ids
            if tag_id in names_by_id
        }
        if getattr(config, "review_tag_enabled", False):
            removed_names.add(
                (getattr(config, "review_tag_name", None) or "KI-prüfen").strip().casefold()
            )
        if getattr(config, "tag_ideas_tag_enabled", False):
            removed_names.add(
                (getattr(config, "tag_ideas_tag_name", None) or "KI-tag-ideen").strip().casefold()
            )

        result.removed_tags = [
            tag for tag in (result.existing_tags or [])
            if str(tag).strip().casefold() in removed_names
        ]
        if not _is_tax_document(result, doc_content):
            result.removed_tags.extend(
                tag for tag in (result.existing_tags or [])
                if str(tag).strip().casefold() == "steuern"
                and tag not in result.removed_tags
            )

        if getattr(config, "classification_tag_enabled", False):
            tag_name = (
                getattr(config, "classification_tag_name", None) or "KI-klassifiziert"
            ).strip()
            if tag_name and tag_name.casefold() not in {tag.casefold() for tag in result.tags}:
                result.tags.append(tag_name)

    async def _post_process(
        self, result: ClassificationResult, config: ClassifierConfig,
        doc_content: str = "", source_created_date: Optional[str] = None,
    ):
        """Post-process: normalize fields, filter tags, verify coherence."""
        logger.info(f"Post-process start: tags_from_model={result.tags}")
        payable_notice = _is_payable_notice(result, doc_content)
        if payable_notice:
            logger.info(
                "Document-type policy: payable contribution/fee notice -> Eingangsrechnung"
            )
            result.document_type = "Eingangsrechnung"
            result.debug_info["document_type_correction"] = (
                "Zahlungsanforderung für Beitrag/Gebühr: buchhalterisch Eingangsrechnung"
            )
        await self._align_custom_fields_with_document_type(result, config)
        if payable_notice:
            self._populate_payable_notice_fields(result, doc_content)
        result.custom_fields = self._normalize_custom_fields(result.custom_fields)
        self._enforce_incoming_invoice_fields(result, doc_content, source_created_date)

        fields_by_name = {
            str(key).strip().casefold(): key for key in (result.custom_fields or {})
        }
        invoice_number_key = fields_by_name.get("rechnungsnummer")
        if (
            invoice_number_key is not None
            and result.custom_fields.get(invoice_number_key) in (None, "")
            and result.document_type in _TRANSACTION_TITLE_TYPES
        ):
            receipt_reference = _extract_receipt_reference(
                doc_content + "\n" + (result.title if result.title else "")
            )
            if receipt_reference:
                result.custom_fields[invoice_number_key] = receipt_reference
                logger.info(
                    "Receipt reference fallback: set Rechnungsnummer to %s",
                    receipt_reference,
                )

        # Applicability alignment intentionally creates internal null slots so
        # deterministic rules above can populate fields such as due date or a
        # receipt reference. Empty slots are implementation details and must
        # not appear in the user-visible proposal or be sent to Paperless.
        result.custom_fields = _omit_empty_custom_fields(result.custom_fields)

        # Remove obvious personal-number patterns (YYYY-NNNNN) from title
        if result.title:
            cleaned = _clean_title(result.title)
            if cleaned != result.title:
                logger.info(f"Title cleaned: '{result.title}' → '{cleaned}'")
                result.title = cleaned

        if result.storage_path_id:
            all_paths = await self.paperless.get_storage_paths(use_cache=True)
            for p in all_paths:
                if p.get("id") == result.storage_path_id:
                    result.storage_path_name = p.get("name", "")
                    break

        # The practice greenfield paths form a complete document-type taxonomy.
        # Where these exact paths exist, use the deterministic policy instead of
        # allowing wording such as "Versicherung" to move an accounting document
        # from Buchhaltung to Verträge.
        accounting_types = {
            "Eingangsrechnung", "Ausgangsrechnung", "Zahlungsbeleg", "Kontoauszug",
            "Kreditkartenabrechnung",
        }
        administration_types = {
            "Bescheid", "Bescheinigung", "Korrespondenz", "Informationsmaterial",
            "Versanddokument",
        }
        policy_paths = []
        if result.document_type in accounting_types:
            policy_paths = ["Buchhaltung", "Finanzen"]
        elif result.document_type == "Personalunterlage":
            policy_paths = ["Personal"]
        elif result.document_type == "Vertrag":
            policy_paths = ["Verträge"]
        elif result.document_type in administration_types:
            policy_paths = ["Verwaltung"]
        elif result.document_type == "Unbekannt":
            policy_paths = ["Unsortiert"]

        if policy_paths:
            all_paths = await self.paperless.get_storage_paths(use_cache=True)
            target_path = next(
                (p for name in policy_paths for p in all_paths if p.get("name") == name),
                None,
            )
            if target_path and result.storage_path_id != target_path.get("id"):
                policy_path = target_path["name"]
                logger.info(
                    "StoragePath policy: document type '%s' -> '%s' (replaced '%s')",
                    result.document_type, policy_path, result.storage_path_name or result.storage_path_id,
                )
                result.storage_path_id = target_path["id"]
                result.storage_path_name = policy_path
                result.storage_path_reason = f"Feste Greenfield-Regel für Dokumenttyp {result.document_type}"

        # --- Storage path behavior: revert to existing if behavior says so ---
        sp_behavior = getattr(config, "storage_path_behavior", "always") or "always"
        if sp_behavior != "always" and result.existing_storage_path_id:
            existing_name = (result.existing_storage_path_name or "").strip().lower()
            keep_existing = False
            if sp_behavior == "keep_if_set":
                keep_existing = True
            elif sp_behavior == "keep_except_list":
                override_names = [n.lower() for n in (getattr(config, "storage_path_override_names", None) or ["Zuweisen"])]
                keep_existing = existing_name not in override_names
            if keep_existing:
                ai_suggestion = result.storage_path_name or f"ID {result.storage_path_id}"
                ai_reason = result.storage_path_reason or ""
                logger.info(
                    f"Post-process: reverting storage path to existing '{result.existing_storage_path_name}' "
                    f"(id={result.existing_storage_path_id}) due to behavior='{sp_behavior}'"
                )
                result.storage_path_id = result.existing_storage_path_id
                result.storage_path_name = result.existing_storage_path_name
                result.storage_path_reason = (
                    f"Bestehender Pfad beibehalten (Regel: {sp_behavior}). "
                    f"KI-Vorschlag war: {ai_suggestion}"
                    + (f" – {ai_reason}" if ai_reason else "")
                )

        if result.correspondent:
            # Check against ignore list first
            corr_ignore = getattr(config, "correspondent_ignore", None) or []
            corr_ignore_lower = [n.lower().strip() for n in corr_ignore if n.strip()]
            corr_name_lower = result.correspondent.lower()
            ignored_by = next(
                (ign for ign in corr_ignore_lower
                 if ign in corr_name_lower or corr_name_lower in ign),
                None,
            )
            if ignored_by:
                logger.info(f"Correspondent ignored: '{result.correspondent}' (matched ignore entry '{ignored_by}')")
                result.correspondent = None
                result.correspondent_is_new = False

        if result.correspondent:
            # Strip legal forms if enabled (post-processing, independent of prompt option)
            if getattr(config, "correspondent_strip_legal", False):
                original = result.correspondent
                result.correspondent = _strip_legal_forms(result.correspondent)
                if result.correspondent != original:
                    logger.info(f"Correspondent legal-strip: '{original}' → '{result.correspondent}'")

            all_correspondents = await self.paperless.get_correspondents(use_cache=False)
            existing_corr_names = {c["name"].lower() for c in all_correspondents}
            result.correspondent_is_new = result.correspondent.lower() not in existing_corr_names

        if result.tags:
            all_tags = await self.paperless.get_tags(use_cache=True)
            existing_tag_names = {t["name"].lower() for t in all_tags}

            all_doc_types = await self.paperless.get_document_types(use_cache=True)
            all_doc_type_names = {dt["name"].lower() for dt in all_doc_types}

            all_correspondents = await self.paperless.get_correspondents(use_cache=True)
            all_corr_names = {c["name"].lower() for c in all_correspondents}

            # Build ignore matchers: exact strings + wildcard patterns
            ignore_exact = set()
            ignore_patterns = []
            for t in (config.tags_ignore or []):
                if "*" in t:
                    regex_pat = re.escape(t).replace(r"\*", ".*")
                    ignore_patterns.append(re.compile(f"^{regex_pat}$", re.IGNORECASE))
                else:
                    ignore_exact.add(t.lower())

            def _is_ignored(tag_name: str) -> bool:
                if tag_name.lower() in ignore_exact:
                    return True
                return any(p.match(tag_name) for p in ignore_patterns)

            filtered_tags = []
            for tag in result.tags:
                tag_lower = tag.lower()
                if tag_lower in all_doc_type_names:
                    logger.info(f"Tag '{tag}' removed: matches a document type name")
                    continue
                if tag_lower in all_corr_names:
                    logger.info(f"Tag '{tag}' removed: matches a correspondent name")
                    continue
                if _is_ignored(tag):
                    logger.info(f"Tag '{tag}' removed: matches ignore pattern")
                    continue
                filtered_tags.append(tag)

            result.tags = filtered_tags
            result.tags_new = [t for t in result.tags if t.lower() not in existing_tag_names]
            # Enforce tag_behavior: in "existing_only" mode the model must not introduce
            # new tags. Drop any proposed tag that does not already exist in Paperless,
            # otherwise tag ideas keep appearing despite the setting being off.
            if getattr(config, "tag_behavior", "existing_only") == "existing_only" and result.tags_new:
                logger.info(f"tag_behavior=existing_only: dropping new tag ideas {result.tags_new}")
                result.tags = [t for t in result.tags if t.lower() in existing_tag_names]
                result.tags_new = []
            logger.info(f"Post-process filtered tags: {result.tags} (new: {result.tags_new})")

        # --- Filter ignored dates ---
        if result.created_date and config.dates_ignore:
            normalized_result_date = self._normalize_date(result.created_date)
            for ignored in config.dates_ignore:
                if normalized_result_date and normalized_result_date == self._normalize_date(ignored):
                    logger.info(f"Date '{result.created_date}' matches ignore list ('{ignored}') -- cleared")
                    result.created_date = None
                    break

        self._verify_result_coherence(result, config, doc_content)

        # Coherence rules may add structural tags after the first tag filter.  The
        # user's existing-only policy is the final authority: neither the model nor
        # a built-in rule may surface a tag that is absent from Paperless.
        if result.tags and getattr(config, "tag_behavior", "existing_only") == "existing_only":
            all_tags = await self.paperless.get_tags(use_cache=True)
            existing_by_lower = {t["name"].lower(): t["name"] for t in all_tags}
            unknown_tags = [t for t in result.tags if t.lower() not in existing_by_lower]
            if unknown_tags:
                logger.info(
                    "Final existing-only filter removed unknown tags: %s",
                    unknown_tags,
                )
            result.tags = [
                existing_by_lower[t.lower()]
                for t in result.tags
                if t.lower() in existing_by_lower
            ]
            result.tags_new = []

        # Status-tag policies are applied later as well, but must already be
        # visible in the review UI so the proposal matches the eventual update.
        await self._project_processing_tags(result, config, doc_content)
        _add_preview_status_tags(result, config)

        canonical_title = _format_transaction_title(result)
        if canonical_title and canonical_title != result.title:
            logger.info("Canonical transaction title: '%s' -> '%s'", result.title, canonical_title)
            result.title = canonical_title

    async def classify_document(self, document_id: int) -> ClassificationResult:
        """Classify a single document and return proposals."""
        # Fresh Paperless data for every classification — new tags/correspondents
        # created by a previous apply must be visible immediately.
        from app.services.cache import get_cache
        await get_cache().clear("paperless:")

        config = await self.get_config()
        provider = await self._build_provider(config)

        document, doc_data = await self._build_document_context(document_id)
        if not document:
            return ClassificationResult(error=f"Document {document_id} not found")

        config_dict = self._build_config_dict(config)
        result = await provider.classify(document, config_dict)

        # Set existing metadata BEFORE _post_process so behavior logic can use it
        result.existing_tags = document.current_tags
        result.existing_correspondent = document.current_correspondent
        result.existing_document_type = document.current_document_type
        result.existing_storage_path_name = document.current_storage_path

        # Resolve existing storage path ID
        existing_sp_id = None
        if document.current_storage_path:
            all_paths = await self.paperless.get_storage_paths(use_cache=True)
            for p in all_paths:
                if p["name"] == document.current_storage_path:
                    existing_sp_id = p["id"]
                    break
        result.existing_storage_path_id = existing_sp_id

        # --- Fallback: if LLM returned nothing, keep the existing value ---
        if not result.title and document.current_title:
            result.title = document.current_title
            logger.info(f"Title fallback: kept existing '{document.current_title}'")
        if not result.correspondent and document.current_correspondent:
            result.correspondent = document.current_correspondent
            logger.info(f"Correspondent fallback: kept existing '{document.current_correspondent}'")
        if not result.document_type and document.current_document_type:
            result.document_type = document.current_document_type
            logger.info(f"DocType fallback: kept existing '{document.current_document_type}'")
        if result.storage_path_id is None and existing_sp_id:
            result.storage_path_id = existing_sp_id
            result.storage_path_name = document.current_storage_path
            result.storage_path_reason = "Vorhandener Speicherpfad beibehalten"
            logger.info(f"StoragePath fallback: kept existing id={existing_sp_id} '{document.current_storage_path}'")

        await self._post_process(result, config, document.content, document.created_date)
        await self._set_missing_required_custom_fields(
            document_id, result, doc_data, config,
        )

        # Remove old "pending" entries for this document before inserting the new one.
        # This prevents stale results from appearing in history / being loaded again.
        await self.db.execute(
            sa_delete(ClassificationHistory)
            .where(ClassificationHistory.document_id == document_id)
            .where(ClassificationHistory.status == "pending")
        )

        classifier_provider_name = await self._get_classifier_provider_name()
        try:
            llm_prov = await self._get_llm_provider(classifier_provider_name)
            history_model = llm_prov.classifier_model or llm_prov.model
        except Exception:
            history_model = "unknown"

        history = ClassificationHistory(
            document_id=document_id,
            document_title=doc_data.get("title", ""),
            provider=classifier_provider_name,
            model=history_model,
            result_json=asdict(result),
            tokens_input=result.tokens_input,
            tokens_output=result.tokens_output,
            cost_usd=result.cost_usd,
            duration_seconds=result.duration_seconds,
            tool_calls_count=result.tool_calls_count,
            status="error" if result.error else "pending",
            error_message=result.error or "",
        )
        self.db.add(history)
        await self.db.commit()

        return result

    async def _set_missing_required_custom_fields(
        self,
        document_id: int,
        result: ClassificationResult,
        doc_data: Dict[str, Any],
        config: ClassifierConfig,
    ) -> None:
        """Validate required values from the proposal and the live document."""
        result.missing_required_custom_fields = []
        if not getattr(config, "enable_custom_fields", False) or not result.document_type:
            return

        selected_type = result.document_type.casefold()
        mappings = [
            mapping for mapping in await self.get_custom_field_mappings()
            if mapping.enabled and selected_type in {
                str(name).casefold() for name in (mapping.required_document_types or [])
            }
        ]
        if not mappings:
            return

        definitions = {
            field.get("id"): field
            for field in await self.paperless.get_custom_fields(use_cache=True)
        }
        existing = {
            row.get("field"): row.get("value")
            for row in (doc_data.get("custom_fields") or [])
            if isinstance(row, dict) and row.get("field") is not None
        }
        proposed_by_name = {
            str(name).strip().casefold(): value
            for name, value in (result.custom_fields or {}).items()
        }
        missing = []
        for mapping in mappings:
            definition = definitions.get(mapping.paperless_field_id, {})
            proposed = proposed_by_name.get(mapping.paperless_field_name.strip().casefold())
            if _custom_field_value_is_valid(mapping, proposed, definition):
                continue
            if _custom_field_value_is_valid(
                mapping, existing.get(mapping.paperless_field_id), definition,
            ):
                continue
            missing.append(mapping.paperless_field_name)

        result.missing_required_custom_fields = sorted(missing, key=str.casefold)
        if missing:
            result.debug_info["missing_required_custom_fields"] = result.missing_required_custom_fields
            classified_name = (
                getattr(config, "classification_tag_name", None) or "KI-klassifiziert"
            ).strip()
            result.tags = [
                tag for tag in result.tags
                if str(tag).strip().casefold() != classified_name.casefold()
            ]
            retry_q = await self.db.execute(
                select(ClassifierOcrRetryState).where(
                    ClassifierOcrRetryState.document_id == document_id
                )
            )
            retry_exists = retry_q.scalar_one_or_none() is not None
            projected_tag = (
                (getattr(config, "review_tag_name", None) or "KI-prüfen").strip()
                if retry_exists else FORCE_OCR_TAG_NAME
            )
            if projected_tag.casefold() not in {
                str(tag).strip().casefold() for tag in result.tags
            }:
                result.tags.append(projected_tag)
            removed_names = {
                OCR_FINISH_TAG_NAME.casefold(),
                CLASSIFICATION_TRIGGER_TAG_NAME.casefold(),
                classified_name.casefold(),
            }
            for tag in result.existing_tags or []:
                if (
                    str(tag).strip().casefold() in removed_names
                    and tag not in result.removed_tags
                ):
                    result.removed_tags.append(tag)

    async def _build_provider_by_name(
        self, provider_name: str, config: ClassifierConfig,
        model_override: Optional[str] = None,
    ) -> BaseClassifierProvider:
        """Build a specific provider with optional model override (for benchmarks)."""
        storage_profiles = await self.get_active_storage_profiles()
        field_mappings = await self.get_custom_field_mappings()
        tool_executor = self._build_tool_executor(config, storage_profiles, field_mappings)

        return await self._create_provider_instance(provider_name, tool_executor, model_override)

    async def benchmark_document(
        self, document_id: int,
        slots: List[tuple],
    ) -> Dict[str, Any]:
        """Run classification with N provider/model combos, strictly sequential."""
        import asyncio

        config = await self.get_config()
        document, doc_data = await self._build_document_context(document_id)
        if not document:
            return {"error": f"Document {document_id} not found"}

        config_dict = self._build_config_dict(config)

        # Resolve existing storage path ID once (shared across benchmark slots)
        bench_existing_sp_id = None
        if document.current_storage_path:
            all_paths = await self.paperless.get_storage_paths(use_cache=True)
            for p in all_paths:
                if p["name"] == document.current_storage_path:
                    bench_existing_sp_id = p["id"]
                    break

        async def run_single(name: str, model: Optional[str]) -> Dict[str, Any]:
            configured_provider = await self._get_llm_provider(name)
            actual_model = model or configured_provider.classifier_model or configured_provider.model
            try:
                provider = await self._build_provider_by_name(name, config, model)
                result = await provider.classify(document, config_dict)
                # Set existing metadata before _post_process so behavior logic works
                result.existing_tags = document.current_tags
                result.existing_correspondent = document.current_correspondent
                result.existing_document_type = document.current_document_type
                result.existing_storage_path_name = document.current_storage_path
                result.existing_storage_path_id = bench_existing_sp_id
                await self._post_process(result, config, document.content)
                return {
                    "provider": name,
                    "model": actual_model,
                    "result": asdict(result),
                }
            except Exception as e:
                logger.error(f"Benchmark {name}/{actual_model} failed: {e}", exc_info=True)
                return {
                    "provider": name,
                    "model": actual_model,
                    "result": asdict(ClassificationResult(error=str(e))),
                }

        # All slots run strictly sequential to avoid GPU contention
        all_results = []
        for name, model in slots:
            r = await run_single(name, model)
            all_results.append(r)

        return {
            "document_id": document_id,
            "document_title": doc_data.get("title", ""),
            "results": all_results,
        }

    async def _resolve_correspondent(self, name: str, config) -> Optional[Dict[str, Any]]:
        """Resolve a proposed correspondent name to a Paperless correspondent.

        Opt-in Beta "smart matching": when ``correspondent_smart_match`` is ON, an
        existing correspondent is REUSED if the proposed name matches it after
        normalization (Tier A) or — only with the extra ``correspondent_smart_fuzzy``
        flag — a guarded fuzzy match (Tier B). When OFF (default) this is byte-identical
        to the previous inline behavior: just ``get_or_create_correspondent(name)``.
        """
        if not bool(getattr(config, "correspondent_smart_match", False)):
            return await self.paperless.get_or_create_correspondent(name)

        # use_cache=True on purpose: create_correspondent invalidates this cache, so a
        # freshly-created name becomes matchable next document; worst case staleness is
        # creating a new correspondent (= today's behavior). Avoids N full HTTP fetches
        # per auto-run over hundreds of correspondents.
        existing = await self.paperless.get_correspondents(use_cache=True)
        name_to_corr: Dict[str, Any] = {}
        for c in existing:
            cname = c.get("name")
            if cname:
                name_to_corr.setdefault(cname, c)

        match = match_correspondent(
            name,
            list(name_to_corr.keys()),
            threshold=(getattr(config, "correspondent_smart_threshold", 90) or 90) / 100.0,
            strip_legal=bool(getattr(config, "correspondent_smart_normalize", True)),
            allow_fuzzy=bool(getattr(config, "correspondent_smart_fuzzy", False)),
        )
        if match and match.matched_name in name_to_corr:
            logger.info(
                "Smart-match correspondent: proposed=%r -> existing=%r (ratio=%.3f, reason=%s, runner_up=%r@%.3f)",
                name, match.matched_name, match.ratio, match.reason, match.runner_up, match.runner_up_ratio,
            )
            return name_to_corr[match.matched_name]

        logger.info("Smart-match: no confident match for %r -> creating new", name)
        return await self.paperless.get_or_create_correspondent(name)

    async def apply_classification(
        self, document_id: int, classification: Dict[str, Any],
        tags_authoritative: bool = False,
    ) -> Dict[str, Any]:
        """Apply a (potentially edited) classification to a document in Paperless.

        When tags_authoritative is True (manual / review apply) the provided tag list is
        treated as the final set: no merge with the document's existing tags, and an
        explicit empty list clears all tags. For automatic classification it stays False
        so tags_keep_existing can still protect manually-assigned tags.
        """
        config = await self.get_config()
        update_data = {}

        if classification.get("title"):
            update_data["title"] = classification["title"]

        created = classification.get("created_date")
        if created and created != "null" and re.match(r"\d{4}-\d{2}-\d{2}", str(created)):
            update_data["created"] = created

        # Resolve tags to IDs. In authoritative mode an explicit (even empty) tag list
        # must be applied, so enter the block whenever a "tags" key is present.
        has_tags_key = "tags" in classification and classification.get("tags") is not None
        if classification.get("tags") or (tags_authoritative and has_tags_key):
            all_tags = await self.paperless.get_tags(use_cache=True)
            tag_name_to_id = {t["name"].lower(): t["id"] for t in all_tags}
            tag_id_to_name = {t["id"]: t["name"] for t in all_tags}
            excluded_ids = set(config.excluded_tag_ids or [])
            authoritative_existing_ids = set()
            if tags_authoritative:
                authoritative_doc = await self.paperless.get_document(document_id)
                authoritative_existing_ids = set((authoritative_doc or {}).get("tags", []))
            tag_ids = []
            for tag_name in (classification.get("tags") or []):
                tid = tag_name_to_id.get(tag_name.lower())
                if tid:
                    if tid in excluded_ids and tid not in authoritative_existing_ids:
                        logger.info(f"Apply: skipping excluded tag '{tag_name}' (id={tid})")
                        continue
                    tag_ids.append(tid)
                else:
                    new_tag = await self.paperless.get_or_create_tag(tag_name)
                    if new_tag:
                        if new_tag["id"] in excluded_ids:
                            logger.info(f"Apply: skipping newly-created excluded tag '{tag_name}'")
                            continue
                        tag_ids.append(new_tag["id"])

            if tags_authoritative:
                # Human-curated list wins: do NOT merge existing doc tags back in, so a
                # tag the user removed stays removed. An empty list clears all tags.
                update_data["tags"] = tag_ids
            else:
                doc = await self.paperless.get_document(document_id)
                existing_tag_ids = doc.get("tags", []) if doc else []

                if config.tags_keep_existing:
                    for etid in existing_tag_ids:
                        if etid not in tag_ids:
                            tag_ids.append(etid)
                else:
                    # Replacing mode: keep protected tags from existing document
                    protected_patterns = self._build_protected_matchers(config)
                    if protected_patterns:
                        for etid in existing_tag_ids:
                            tag_name = tag_id_to_name.get(etid, "")
                            if etid not in tag_ids and self._is_tag_protected(tag_name, protected_patterns):
                                tag_ids.append(etid)
                                logger.info(f"Apply: keeping protected tag '{tag_name}' (id={etid})")

                if tag_ids:
                    update_data["tags"] = tag_ids

        # Only the classifier-owned processing marker is replaced. Other tags in
        # auto_classify_only_tag_ids (for example ocrfinish) are prerequisites and
        # must remain on the document.
        all_tags_for_triggers = await self.paperless.get_tags(use_cache=True)
        trigger_tag_ids = classification_trigger_tag_ids(
            all_tags_for_triggers,
            getattr(config, "auto_classify_only_tag_ids", None) or [],
        )
        if trigger_tag_ids:
            if "tags" in update_data:
                current_tag_ids = list(update_data["tags"])
            else:
                doc = await self.paperless.get_document(document_id)
                current_tag_ids = list((doc or {}).get("tags", []))
            filtered_tag_ids = [tag_id for tag_id in current_tag_ids if tag_id not in trigger_tag_ids]
            if filtered_tag_ids != current_tag_ids:
                update_data["tags"] = filtered_tag_ids
                logger.info(
                    "Apply: removed trigger tag(s) %s from doc %s",
                    sorted(trigger_tag_ids.intersection(current_tag_ids)), document_id,
                )

        # Automatic apply preserves existing tags by default. Explicit removals
        # projected during analysis must still win (for example an invalid
        # pre-existing "Steuern" tag).
        removed_tag_names = {
            str(name).strip().casefold()
            for name in (classification.get("removed_tags") or [])
            if str(name).strip()
        }
        if removed_tag_names:
            all_tags_for_removal = await self.paperless.get_tags(use_cache=True)
            removed_tag_ids = {
                tag["id"] for tag in all_tags_for_removal
                if str(tag.get("name") or "").strip().casefold() in removed_tag_names
            }
            if removed_tag_ids:
                if "tags" in update_data:
                    current_tag_ids = list(update_data["tags"])
                else:
                    doc = await self.paperless.get_document(document_id)
                    current_tag_ids = list((doc or {}).get("tags", []))
                filtered_tag_ids = [
                    tag_id for tag_id in current_tag_ids
                    if tag_id not in removed_tag_ids
                ]
                if filtered_tag_ids != current_tag_ids:
                    update_data["tags"] = filtered_tag_ids
                    logger.info(
                        "Apply: removed projected tag(s) %s from doc %s",
                        sorted(removed_tag_ids.intersection(current_tag_ids)), document_id,
                    )

        # Classification Tag: if enabled, ensure the configured tag is on every classified document
        if getattr(config, "classification_tag_enabled", False):
            tag_name = (getattr(config, "classification_tag_name", None) or "KI-klassifiziert").strip()
            if tag_name:
                cls_tag = await self.paperless.get_or_create_tag(tag_name)
                if cls_tag:
                    if "tags" in update_data:
                        current_tag_ids = list(update_data["tags"])
                    else:
                        # tags not set by this apply — load existing tags from document
                        doc = await self.paperless.get_document(document_id)
                        current_tag_ids = doc.get("tags", []) if doc else []
                    if cls_tag["id"] not in current_tag_ids:
                        current_tag_ids.append(cls_tag["id"])
                        update_data["tags"] = current_tag_ids
                        logger.info(f"Apply: added classification tag '{tag_name}' (id={cls_tag['id']}) to doc {document_id}")

        # Remove Review-Tag after apply (if configured)
        if getattr(config, "review_tag_enabled", False):
            review_tag_name = (getattr(config, "review_tag_name", None) or "KI-prüfen").strip()
            if review_tag_name:
                try:
                    all_tags = await self.paperless.get_tags(use_cache=False)
                    review_tag_obj = next((t for t in all_tags if t["name"] == review_tag_name), None)
                    if review_tag_obj:
                        if "tags" in update_data:
                            current_tag_ids = list(update_data["tags"])
                        else:
                            doc = await self.paperless.get_document(document_id)
                            current_tag_ids = doc.get("tags", []) if doc else []
                        if review_tag_obj["id"] in current_tag_ids:
                            current_tag_ids = [t for t in current_tag_ids if t != review_tag_obj["id"]]
                            update_data["tags"] = current_tag_ids
                            logger.info(f"Apply: removed review tag '{review_tag_name}' (id={review_tag_obj['id']}) from doc {document_id}")
                except Exception as e:
                    logger.warning(f"Could not remove review tag: {e}")

        # Also remove tag-ideas tag after apply (if configured)
        if getattr(config, "tag_ideas_tag_enabled", False):
            ideas_tag_name = (getattr(config, "tag_ideas_tag_name", None) or "KI-tag-ideen").strip()
            if ideas_tag_name:
                try:
                    all_tags = await self.paperless.get_tags(use_cache=False)
                    ideas_tag_obj = next((t for t in all_tags if t["name"] == ideas_tag_name), None)
                    if ideas_tag_obj:
                        if "tags" in update_data:
                            current_tag_ids = list(update_data["tags"])
                        else:
                            doc = await self.paperless.get_document(document_id)
                            current_tag_ids = doc.get("tags", []) if doc else []
                        if ideas_tag_obj["id"] in current_tag_ids:
                            current_tag_ids = [t for t in current_tag_ids if t != ideas_tag_obj["id"]]
                            update_data["tags"] = current_tag_ids
                            logger.info(f"Apply: removed tag-ideas tag '{ideas_tag_name}' from doc {document_id}")
                except Exception as e:
                    logger.warning(f"Could not remove tag-ideas tag: {e}")

        # Resolve correspondent (opt-in smart matching can reuse an existing one
        # instead of creating a near-duplicate; OFF by default = unchanged behavior)
        if classification.get("correspondent"):
            corr = await self._resolve_correspondent(classification["correspondent"], config)
            if corr:
                update_data["correspondent"] = corr["id"]

        # Resolve document type
        if classification.get("document_type"):
            all_types = await self.paperless.get_document_types(use_cache=True)
            for dt in all_types:
                if dt["name"].lower() == classification["document_type"].lower():
                    update_data["document_type"] = dt["id"]
                    break

        # Generic deterministic status-tag rules. These are explicit user policy,
        # not an LLM decision, and therefore work for DATEV as well as any future
        # workflow tag without adding another special-case setting.
        status_rules = getattr(config, "status_tag_rules", None) or []
        chosen_type = str(classification.get("document_type") or "").strip().casefold()
        matching_rules = []
        for rule in status_rules:
            if not isinstance(rule, dict) or not rule.get("enabled", True):
                continue
            tag_name = str(rule.get("tag_name") or "").strip()
            allowed_types = {
                str(name).strip().casefold()
                for name in (rule.get("document_types") or [])
                if str(name).strip()
            }
            if tag_name and allowed_types and chosen_type in allowed_types:
                matching_rules.append({
                    "tag_name": tag_name,
                    "tag_id": rule.get("tag_id"),
                    "skip_if_tag_id": rule.get("skip_if_tag_id"),
                    "skip_if_tag_name": str(rule.get("skip_if_tag_name") or "").strip(),
                })

        if matching_rules:
            all_tags = await self.paperless.get_tags(use_cache=False)
            by_id = {tag["id"]: tag for tag in all_tags}
            by_name = {tag["name"].casefold(): tag for tag in all_tags}
            current_doc = await self.paperless.get_document(document_id)
            existing_ids = list((current_doc or {}).get("tags", []))
            current_ids = list(update_data.get("tags", existing_ids))
            for rule in matching_rules:
                tag_name = rule["tag_name"]
                tag_id = rule.get("tag_id")
                blocker = by_id.get(rule.get("skip_if_tag_id")) if rule.get("skip_if_tag_id") else None
                blocker_name = rule.get("skip_if_tag_name") or ""
                if blocker and blocker_name and blocker["name"].casefold() != blocker_name.casefold():
                    blocker = None
                if not blocker and blocker_name:
                    blocker = by_name.get(blocker_name.casefold())
                if blocker and blocker["id"] in existing_ids:
                    logger.info(
                        "Apply: status-tag rule for '%s' skipped because blocker '%s' is present",
                        tag_name, blocker["name"],
                    )
                    continue
                status_tag = by_id.get(tag_id) if tag_id else None
                if status_tag and status_tag["name"].casefold() != tag_name.casefold():
                    status_tag = None
                if not status_tag:
                    status_tag = by_name.get(tag_name.casefold())
                if not status_tag:
                    status_tag = await self.paperless.get_or_create_tag(tag_name)
                if status_tag and status_tag["id"] not in current_ids:
                    current_ids.append(status_tag["id"])
                    logger.info(
                        "Apply: status-tag rule added '%s' to doc %s for type '%s'",
                        tag_name, document_id, classification.get("document_type"),
                    )
            update_data["tags"] = current_ids

        # Storage path -- respect configured behavior
        if classification.get("storage_path_id"):
            sp_behavior = getattr(config, "storage_path_behavior", "always") or "always"
            sp_override_names = getattr(config, "storage_path_override_names", ["Zuweisen"]) or ["Zuweisen"]
            existing_sp_id = classification.get("existing_storage_path_id")
            existing_sp_name = (classification.get("existing_storage_path_name") or "").strip()

            # Fallback: fetch live from Paperless if not provided (extra safety)
            if not existing_sp_id and sp_behavior != "always":
                live_doc = await self.paperless.get_document(document_id)
                if live_doc and live_doc.get("storage_path"):
                    existing_sp_id = live_doc["storage_path"]
                    all_paths = await self.paperless.get_storage_paths(use_cache=True)
                    sp_map = {p["id"]: p["name"] for p in all_paths}
                    existing_sp_name = sp_map.get(existing_sp_id, "")
                    logger.info(f"Storage path fallback from Paperless: '{existing_sp_name}' (id={existing_sp_id})")

            apply_sp = True
            if sp_behavior == "keep_if_set":
                # Never change if document already has a path
                apply_sp = not existing_sp_id
            elif sp_behavior == "keep_except_list":
                # Keep existing UNLESS the current path name is in the override list
                if existing_sp_id:
                    override_names_lower = [n.lower() for n in sp_override_names]
                    apply_sp = existing_sp_name.lower() in override_names_lower
                # If no path set yet, always assign
            # "always" => apply_sp stays True

            if apply_sp:
                update_data["storage_path"] = classification["storage_path_id"]
            else:
                logger.info(
                    f"Storage path skipped (behavior={sp_behavior}): "
                    f"existing='{existing_sp_name}' (id={existing_sp_id})"
                )

        # Custom fields
        if classification.get("custom_fields"):
            field_mappings = await self.get_custom_field_mappings()
            paperless_field_definitions = {
                field.get("id"): field
                for field in await self.paperless.get_custom_fields(use_cache=True)
            }
            selected_type = str(classification.get("document_type") or "").casefold()
            field_name_to_mapping = {m.paperless_field_name: m for m in field_mappings}
            non_applicable_managed_ids = {
                mapping.paperless_field_id
                for mapping in field_mappings
                if mapping.enabled and selected_type not in {
                    name.casefold() for name in effective_custom_field_document_types(
                        mapping.paperless_field_name,
                        mapping.applicable_document_types,
                    )
                }
            }
            custom_field_updates = []
            for field_name, value in classification["custom_fields"].items():
                mapping = field_name_to_mapping.get(field_name)
                allowed_types = {
                    name.casefold() for name in effective_custom_field_document_types(
                        mapping.paperless_field_name if mapping else field_name,
                        mapping.applicable_document_types if mapping else None,
                    )
                }
                applicable = bool(mapping and mapping.enabled and allowed_types) and selected_type in allowed_types
                if mapping and not applicable:
                    logger.info("Apply: custom field '%s' skipped for document type '%s'", field_name, selected_type)
                if mapping and applicable and value is not None:
                    # Sanitize only numeric values. Applying this to strings used to
                    # turn invoice numbers such as "RE-123" into "-123".
                    if isinstance(value, str):
                        cleaned = value.strip()
                        if mapping.paperless_field_type in {"integer", "float", "monetary"}:
                            cleaned = re.sub(r'^[A-Z]{2,3}\s*', '', cleaned)
                            cleaned = re.sub(r'[€$£]\s*', '', cleaned).strip()
                            try:
                                float(cleaned)
                                value = cleaned
                            except ValueError:
                                pass
                        else:
                            value = cleaned
                    field_definition = paperless_field_definitions.get(
                        mapping.paperless_field_id, {}
                    )
                    resolved_value = _resolve_select_custom_field_value(
                        field_definition, value,
                    )
                    if resolved_value is None:
                        logger.warning(
                            "Apply: custom select field '%s' has unknown option %r; skipped",
                            field_name, value,
                        )
                        continue
                    value = resolved_value
                    if mapping.validation_regex and not re.fullmatch(mapping.validation_regex, str(value)):
                        logger.warning("Apply: custom field '%s' rejected by validation", field_name)
                        continue
                    if mapping.paperless_field_type == "integer":
                        value = int(value)
                    elif mapping.paperless_field_type in {"float", "monetary"}:
                        value = float(value)
                    custom_field_updates.append({"field": mapping.paperless_field_id, "value": value})
            if custom_field_updates or non_applicable_managed_ids:
                # PATCH replaces Paperless' complete custom-field list. Preserve
                # integration-owned fields and overwrite only values that this
                # classifier is responsible for.
                live_doc = await self.paperless.get_document(document_id)
                existing_fields = (live_doc or {}).get("custom_fields", []) or []
                merged_fields = {
                    row.get("field"): {"field": row.get("field"), "value": row.get("value")}
                    for row in existing_fields
                    if isinstance(row, dict)
                    and row.get("field") is not None
                    and row.get("field") not in non_applicable_managed_ids
                }
                for row in custom_field_updates:
                    merged_fields[row["field"]] = row
                update_data["custom_fields"] = list(merged_fields.values())

        if not update_data:
            return {"applied": False, "reason": "No changes to apply"}

        logger.info(f"Applying to doc {document_id}: {update_data}")
        result = await self.paperless.update_document(document_id, update_data)

        # Mark latest pending/review history entry for this document as applied
        try:
            from sqlalchemy import select, desc
            from app.models.classifier import ClassificationHistory
            hist_q = await self.db.execute(
                select(ClassificationHistory)
                .where(ClassificationHistory.document_id == document_id)
                .where(ClassificationHistory.status.in_(["pending", "review"]))
                .order_by(desc(ClassificationHistory.id))
                .limit(1)
            )
            hist = hist_q.scalars().first()
            if hist:
                hist.status = "applied"
                await self.db.commit()
                logger.info(f"History entry {hist.id} marked as applied for doc {document_id} (was: {hist.status})")
        except Exception as e:
            logger.warning(f"Could not update history status: {e}")

        # Always refresh cache after apply -- new tags/correspondents must be
        # visible immediately for the next classification call.
        from app.services.cache import get_cache
        cache = get_cache()
        await cache.clear("paperless:")
        logger.info("Cache cleared after apply -- next classification gets fresh Paperless data")

        return {"applied": True, "updated_fields": list(update_data.keys()), "result": result}

    @staticmethod
    def _needs_review(result: ClassificationResult) -> str:
        """Check if a classification result needs manual review. Returns reason or empty string.

        Only triggers for real problems — not for normal new correspondents/tags,
        since those are clearly visible in the history view.
        """
        reasons = []

        if result.error:
            reasons.append("Fehler bei Klassifizierung")
        if not result.title:
            reasons.append("Kein Titel erkannt")
        if not result.correspondent and not result.document_type:
            reasons.append("Weder Korrespondent noch Dokumenttyp erkannt")
        if result.missing_required_custom_fields:
            reasons.append(
                "Pflichtfelder fehlen oder sind ungültig: "
                + ", ".join(result.missing_required_custom_fields)
            )
        # Tags are optional by design.  The tag prompt explicitly permits []
        # when none of the curated business tags fits the document.  Treating
        # that valid result as an error sent every such document to review.

        return "; ".join(reasons)

    async def classify_document_auto(self, document_id: int, mode: str = "review") -> Dict[str, Any]:
        """Classify a document in auto-mode.

        New tags suggested by the AI are NOT created automatically.
        Instead they are saved as 'tag_ideas' on the history entry for
        manual review. The document gets classified with existing tags only.
        """
        result = await self.classify_document(document_id)
        review_reason = self._needs_review(result)
        config = await self.get_config()

        if result.error:
            return {"document_id": document_id, "action": "error", "reason": result.error}

        if result.missing_required_custom_fields:
            retry_q = await self.db.execute(
                select(ClassifierOcrRetryState).where(
                    ClassifierOcrRetryState.document_id == document_id
                )
            )
            retry_state = retry_q.scalar_one_or_none()
            if retry_state is None:
                await self._queue_force_ocr(
                    document_id, result.missing_required_custom_fields, config,
                )
                return {
                    "document_id": document_id,
                    "action": "force_ocr",
                    "reason": "Pflichtfelder fehlen oder sind ungültig; erneute OCR eingeplant",
                    "missing_required_custom_fields": result.missing_required_custom_fields,
                }
            await self._route_required_fields_to_review(document_id, config)
        else:
            await self.db.execute(
                sa_delete(ClassifierOcrRetryState).where(
                    ClassifierOcrRetryState.document_id == document_id
                )
            )
            await self.db.commit()

        # Sync tags_new with actual result.tags (verification may have removed some)
        if result.tags_new and result.tags:
            final_tag_set = {t.lower() for t in result.tags}
            result.tags_new = [t for t in result.tags_new if t.lower() in final_tag_set]

        # Extract new tag ideas before applying
        tag_ideas = list(result.tags_new) if result.tags_new else []
        has_tag_ideas = len(tag_ideas) > 0

        # Build a version of the result that only uses existing tags
        apply_data = asdict(result)
        if has_tag_ideas:
            existing_tags = [t for t in (result.tags or []) if t not in tag_ideas]
            apply_data["tags"] = existing_tags
            apply_data["tags_new"] = []
            logger.info(
                f"Auto-classify doc {document_id}: {len(tag_ideas)} tag idea(s) saved: {tag_ideas}"
            )

        if mode == "auto_apply" and not review_reason:
            await self.apply_classification(document_id, apply_data)
            # Save tag ideas on the history entry
            if has_tag_ideas:
                await self._save_tag_ideas(document_id, tag_ideas)
                if getattr(config, "tag_ideas_tag_enabled", False):
                    await self._add_status_tag(document_id, getattr(config, "tag_ideas_tag_name", None) or "KI-tag-ideen")
            return {
                "document_id": document_id,
                "action": "applied",
                "tag_ideas": tag_ideas,
            }

        # Mark as "review" in history if needed
        if review_reason:
            try:
                hist_q = await self.db.execute(
                    select(ClassificationHistory)
                    .where(ClassificationHistory.document_id == document_id)
                    .where(ClassificationHistory.status == "pending")
                    .order_by(ClassificationHistory.id.desc())
                    .limit(1)
                )
                hist = hist_q.scalars().first()
                if hist:
                    hist.status = "review"
                    hist.error_message = review_reason
                    if has_tag_ideas:
                        hist.tag_ideas = tag_ideas
                    await self.db.commit()
            except Exception as e:
                logger.warning(f"Could not mark as review: {e}")
            if getattr(config, "review_tag_enabled", False):
                await self._add_status_tag(document_id, getattr(config, "review_tag_name", None) or "KI-prüfen")
            if has_tag_ideas and getattr(config, "tag_ideas_tag_enabled", False):
                await self._add_status_tag(document_id, getattr(config, "tag_ideas_tag_name", None) or "KI-tag-ideen")
        else:
            # No review needed — auto-apply with existing tags only
            await self.apply_classification(document_id, apply_data)
            if has_tag_ideas:
                await self._save_tag_ideas(document_id, tag_ideas)
                if getattr(config, "tag_ideas_tag_enabled", False):
                    await self._add_status_tag(document_id, getattr(config, "tag_ideas_tag_name", None) or "KI-tag-ideen")
            return {
                "document_id": document_id,
                "action": "applied",
                "tag_ideas": tag_ideas,
            }

        return {
            "document_id": document_id,
            "action": "review" if review_reason else "pending",
            "reason": review_reason,
            "tag_ideas": tag_ideas,
        }

    async def _queue_force_ocr(
        self,
        document_id: int,
        missing_fields: List[str],
        config: ClassifierConfig,
    ) -> None:
        """Switch processing tags and persist the one-shot OCR retry marker."""
        state = ClassifierOcrRetryState(
            document_id=document_id,
            missing_fields=list(missing_fields),
        )
        self.db.add(state)
        try:
            force_tag = await self.paperless.get_or_create_tag(FORCE_OCR_TAG_NAME)
            trigger_tag = await self.paperless.get_or_create_tag(
                CLASSIFICATION_TRIGGER_TAG_NAME
            )
            doc = await self.paperless.get_document(document_id)
            if not doc:
                raise RuntimeError(f"Document {document_id} not found")
            tags = await self.paperless.get_tags(use_cache=False)
            ids_by_name = {
                str(tag.get("name") or "").strip().casefold(): tag.get("id")
                for tag in tags
            }
            remove_names = {
                OCR_FINISH_TAG_NAME.casefold(),
                CLASSIFICATION_TRIGGER_TAG_NAME.casefold(),
                (getattr(config, "classification_tag_name", None) or "KI-klassifiziert").strip().casefold(),
                (getattr(config, "review_tag_name", None) or "KI-prüfen").strip().casefold(),
            }
            remove_ids = {ids_by_name[name] for name in remove_names if ids_by_name.get(name)}
            current_ids = [
                tag_id for tag_id in (doc.get("tags") or [])
                if tag_id not in remove_ids and tag_id != trigger_tag.get("id")
            ]
            if force_tag["id"] not in current_ids:
                current_ids.append(force_tag["id"])
            await self.paperless.update_document(document_id, {"tags": current_ids})

            hist_q = await self.db.execute(
                select(ClassificationHistory)
                .where(ClassificationHistory.document_id == document_id)
                .where(ClassificationHistory.status == "pending")
                .order_by(ClassificationHistory.id.desc())
                .limit(1)
            )
            history = hist_q.scalars().first()
            if history:
                history.status = "ocr_retry"
                history.error_message = (
                    "Pflichtfelder fehlen oder sind ungültig: " + ", ".join(missing_fields)
                )
            await self.db.commit()
            logger.info(
                "Force-OCR queued for doc %s; missing fields: %s",
                document_id, missing_fields,
            )
        except Exception:
            await self.db.rollback()
            raise

    async def _route_required_fields_to_review(
        self, document_id: int, config: ClassifierConfig,
    ) -> None:
        """End the retry cycle and place the document in the manual queue."""
        review_name = (
            getattr(config, "review_tag_name", None) or "KI-prüfen"
        ).strip()
        review_tag = await self.paperless.get_or_create_tag(review_name)
        doc = await self.paperless.get_document(document_id)
        if not doc:
            return
        tags = await self.paperless.get_tags(use_cache=False)
        remove_ids = {
            tag.get("id") for tag in tags
            if str(tag.get("name") or "").strip().casefold() in {
                CLASSIFICATION_TRIGGER_TAG_NAME.casefold(),
                FORCE_OCR_TAG_NAME.casefold(),
            }
        }
        current_ids = [
            tag_id for tag_id in (doc.get("tags") or []) if tag_id not in remove_ids
        ]
        if review_tag["id"] not in current_ids:
            current_ids.append(review_tag["id"])
        await self.paperless.update_document(document_id, {"tags": current_ids})

    async def _save_tag_ideas(self, document_id: int, tag_ideas: List[str]):
        """Save tag ideas on the latest history entry for a document."""
        try:
            hist_q = await self.db.execute(
                select(ClassificationHistory)
                .where(ClassificationHistory.document_id == document_id)
                .order_by(ClassificationHistory.id.desc())
                .limit(1)
            )
            hist = hist_q.scalars().first()
            if hist:
                hist.tag_ideas = tag_ideas
                await self.db.commit()
        except Exception as e:
            logger.warning(f"Could not save tag ideas for doc {document_id}: {e}")

    async def _add_status_tag(self, document_id: int, tag_name: str):
        """Append a single tag to a document in Paperless (creates the tag if missing)."""
        try:
            tag_name = tag_name.strip()
            if not tag_name:
                return
            tag = await self.paperless.get_or_create_tag(tag_name)
            if not tag:
                return
            doc = await self.paperless.get_document(document_id)
            if not doc:
                return
            existing = list(doc.get("tags", []))
            if tag["id"] not in existing:
                existing.append(tag["id"])
                await self.paperless.update_document(document_id, {"tags": existing})
                logger.info(f"Status tag '{tag_name}' (id={tag['id']}) added to doc {document_id}")
        except Exception as e:
            logger.warning(f"Could not add status tag '{tag_name}' to doc {document_id}: {e}")
