from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from app.config import settings
import os

# Ensure data directory exists
os.makedirs("data", exist_ok=True)

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
)

# Session factory
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


async def create_tables():
    """Create all database tables."""
    from app.models import settings_model, merge_history, statistics, classifier, ocr, rag, duplicates, match_log  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Lightweight migrations for new columns on existing tables
        await _migrate_columns(conn)


async def _migrate_columns(conn):
    """Add missing columns to existing tables (safe to run repeatedly)."""
    import sqlalchemy as sa

    migrations = [
        ("classifier_config", "excluded_tag_ids", "TEXT DEFAULT '[]'"),
        ("classifier_config", "excluded_correspondent_ids", "TEXT DEFAULT '[]'"),
        ("classifier_config", "excluded_document_type_ids", "TEXT DEFAULT '[]'"),
        ("classifier_config", "tags_min", "INTEGER DEFAULT 0"),
        ("classifier_config", "tags_max", "INTEGER DEFAULT 5"),
        ("classifier_config", "tags_keep_existing", "BOOLEAN DEFAULT 1"),
        ("classifier_config", "tags_ignore", "TEXT DEFAULT '[]'"),
        ("classifier_config", "prompt_title", "TEXT DEFAULT ''"),
        ("classifier_config", "prompt_tags", "TEXT DEFAULT ''"),
        ("classifier_config", "prompt_correspondent", "TEXT DEFAULT ''"),
        ("classifier_config", "prompt_document_type", "TEXT DEFAULT ''"),
        ("classifier_config", "prompt_date", "TEXT DEFAULT ''"),
        ("classifier_custom_field_mappings", "ignore_values", "TEXT DEFAULT ''"),
        ("classifier_config", "tags_protected", "TEXT DEFAULT '[]'"),
        ("classifier_config", "dates_ignore", "TEXT DEFAULT '[]'"),
        ("classifier_config", "storage_path_behavior", "TEXT DEFAULT 'always'"),
        ("classifier_config", "storage_path_override_names", "TEXT DEFAULT '[\"Zuweisen\"]'"),
        ("classifier_config", "correspondent_trim_prompt", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "correspondent_strip_legal", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "correspondent_ignore", "TEXT DEFAULT '[]'"),
        ("classifier_config", "auto_classify_enabled", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "auto_classify_interval", "INTEGER DEFAULT 5"),
        ("classifier_config", "auto_classify_mode", "TEXT DEFAULT 'review'"),
        ("classifier_config", "auto_classify_skip_tag_ids", "TEXT DEFAULT '[]'"),
        ("classifier_config", "auto_classify_only_tag_ids", "TEXT DEFAULT '[]'"),
        ("classifier_history", "tag_ideas", "TEXT DEFAULT '[]'"),
        # Central LLM Provider table extensions
        ("llm_providers", "classifier_model", "TEXT DEFAULT ''"),
        # Job assignment in app_settings
        ("app_settings", "classifier_provider", "TEXT DEFAULT 'ollama'"),
        # RAG: LLM Query Rewriting + Contextual Retrieval
        ("rag_config", "query_rewrite_enabled", "BOOLEAN DEFAULT 1"),
        ("rag_config", "contextual_retrieval_enabled", "BOOLEAN DEFAULT 0"),
        ("rag_config", "rag_enabled", "BOOLEAN DEFAULT 0"),
        # Classification Tag
        ("classifier_config", "classification_tag_enabled", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "classification_tag_name", "TEXT DEFAULT 'KI-klassifiziert'"),
        ("classifier_config", "review_tag_enabled", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "review_tag_name", "TEXT DEFAULT 'KI-prüfen'"),
        ("classifier_config", "tag_ideas_tag_enabled", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "tag_ideas_tag_name", "TEXT DEFAULT 'KI-tag-ideen'"),
        ("classifier_config", "status_tag_rules", "TEXT DEFAULT '[]'"),
        ("classifier_custom_field_mappings", "applicable_document_types", "TEXT DEFAULT '[]'"),
        ("classifier_custom_field_mappings", "required_document_types", "TEXT DEFAULT '[]'"),
        # Smart correspondent matching (opt-in Beta) — all default OFF/safe
        ("classifier_config", "correspondent_smart_match", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "correspondent_smart_fuzzy", "BOOLEAN DEFAULT 0"),
        ("classifier_config", "correspondent_smart_threshold", "INTEGER DEFAULT 90"),
        ("classifier_config", "correspondent_smart_normalize", "BOOLEAN DEFAULT 1"),
    ]

    for table, column, col_type in migrations:
        try:
            await conn.execute(sa.text(
                f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
            ))
        except Exception:
            pass  # Column already exists

    # Preserve supported legacy settings before obsolete columns are removed.
    await _migrate_classifier_to_providers(conn)
    await _migrate_datev_status_rule(conn)
    await _prune_legacy_schema(conn)


async def _migrate_datev_status_rule(conn):
    """Preserve an existing DATEV rule in the generic status-tag rule list."""
    import json
    import sqlalchemy as sa

    try:
        result = await conn.execute(sa.text(
            "SELECT status_tag_rules, datev_tag_enabled, datev_tag_name, "
            "datev_document_types FROM classifier_config WHERE id = 1"
        ))
        row = result.fetchone()
    except Exception:
        return
    if not row:
        return

    raw_rules, enabled, tag_name, raw_types = row
    try:
        rules = json.loads(raw_rules) if isinstance(raw_rules, str) else (raw_rules or [])
    except Exception:
        rules = []
    if rules:
        changed = False
        for rule in rules:
            if isinstance(rule, dict) and rule.get("id") == "migrated-datev-upload":
                if "skip_if_tag_name" not in rule:
                    rule["skip_if_tag_id"] = None
                    rule["skip_if_tag_name"] = "DATEV-gesendet"
                    changed = True
        if changed:
            await conn.execute(sa.text(
                "UPDATE classifier_config SET status_tag_rules = :rules WHERE id = 1"
            ), {"rules": json.dumps(rules, ensure_ascii=False)})
        return
    if not enabled:
        return
    try:
        document_types = json.loads(raw_types) if isinstance(raw_types, str) else (raw_types or [])
    except Exception:
        document_types = []

    rules = [{
        "id": "migrated-datev-upload",
        "enabled": True,
        "tag_id": None,
        "tag_name": tag_name or "DATEV-Upload",
        "document_types": document_types,
        "skip_if_tag_id": None,
        "skip_if_tag_name": "DATEV-gesendet",
    }]
    await conn.execute(sa.text(
        "UPDATE classifier_config SET status_tag_rules = :rules WHERE id = 1"
    ), {"rules": json.dumps(rules, ensure_ascii=False)})


async def _migrate_classifier_to_providers(conn):
    """Preserve supported OpenAI/Ollama values from the former classifier columns."""
    import sqlalchemy as sa

    try:
        result = await conn.execute(sa.text(
            "SELECT active_provider, openai_model, ollama_host, ollama_model "
            "FROM classifier_config LIMIT 1"
        ))
        row = result.fetchone()
    except Exception:
        return  # No classifier_config yet

    if not row:
        return

    active_provider, openai_model, ollama_host, ollama_model = row

    # OpenAI: set classifier_model if different from default
    if openai_model:
        await conn.execute(sa.text(
            "UPDATE llm_providers SET classifier_model = :m WHERE name = 'openai' AND (classifier_model IS NULL OR classifier_model = '')"
        ), {"m": openai_model})

    # Ollama: set classifier_model + host
    if ollama_model:
        await conn.execute(sa.text(
            "UPDATE llm_providers SET classifier_model = :m WHERE name = 'ollama' AND (classifier_model IS NULL OR classifier_model = '')"
        ), {"m": ollama_model})
    if ollama_host:
        await conn.execute(sa.text(
            "UPDATE llm_providers SET api_base_url = :u WHERE name = 'ollama' AND (api_base_url IS NULL OR api_base_url = '' OR api_base_url = 'http://localhost:11434')"
        ), {"u": ollama_host})

    # Migrate active_provider into app_settings
    if active_provider in {"ollama", "openai"}:
        await conn.execute(sa.text(
            "UPDATE app_settings SET classifier_provider = :p WHERE id = 1 AND (classifier_provider IS NULL OR classifier_provider = '')"
        ), {"p": active_provider})


async def _prune_legacy_schema(conn):
    """Remove unsupported providers and columns no longer represented by models."""
    import sqlalchemy as sa

    await conn.execute(sa.text(
        "INSERT OR IGNORE INTO llm_providers "
        "(name, display_name, api_key, api_base_url, model, classifier_model, is_active, is_configured) "
        "VALUES ('ollama', 'Ollama (Lokal)', '', 'http://localhost:11434', 'llama3.1', '', 1, 1)"
    ))
    await conn.execute(sa.text(
        "INSERT OR IGNORE INTO llm_providers "
        "(name, display_name, api_key, api_base_url, model, classifier_model, is_active, is_configured) "
        "VALUES ('openai', 'OpenAI', '', '', 'gpt-4o-mini', '', 0, 0)"
    ))
    # Keep the two supported provider records and guarantee deterministic
    # fallbacks for installations that previously selected another provider.
    await conn.execute(sa.text(
        "DELETE FROM llm_providers WHERE name NOT IN ('ollama', 'openai')"
    ))
    # Cloud Import was removed because Paperless Classification processes
    # documents exclusively from Paperless-ngx.
    await conn.execute(sa.text("DROP TABLE IF EXISTS cloud_import_log"))
    await conn.execute(sa.text("DROP TABLE IF EXISTS cloud_sources"))
    await conn.execute(sa.text(
        "UPDATE app_settings SET classifier_provider = 'ollama' "
        "WHERE classifier_provider NOT IN ('ollama', 'openai') OR classifier_provider IS NULL"
    ))
    await conn.execute(sa.text(
        "UPDATE rag_config SET embedding_provider = 'ollama' "
        "WHERE embedding_provider NOT IN ('ollama', 'openai') OR embedding_provider IS NULL"
    ))
    await conn.execute(sa.text(
        "UPDATE rag_config SET chat_model_provider = 'ollama' "
        "WHERE chat_model_provider NOT IN ('ollama', 'openai') OR chat_model_provider IS NULL"
    ))

    app_info = await conn.execute(sa.text("PRAGMA table_info(app_settings)"))
    app_columns = {row[1] for row in app_info.fetchall()}
    if "sidebar_compact" in app_columns:
        await conn.execute(sa.text(
            'ALTER TABLE app_settings DROP COLUMN "sidebar_compact"'
        ))

    provider_info = await conn.execute(sa.text("PRAGMA table_info(llm_providers)"))
    provider_columns = {row[1] for row in provider_info.fetchall()}
    if "vision_model" in provider_columns:
        await conn.execute(sa.text(
            'ALTER TABLE llm_providers DROP COLUMN "vision_model"'
        ))

    obsolete_columns = (
        "active_provider", "openai_model", "mistral_api_key", "mistral_model",
        "openrouter_api_key", "openrouter_model", "ollama_host", "ollama_model",
        "auto_classify_filter_mode", "datev_tag_enabled", "datev_tag_name",
        "datev_document_types",
    )
    info = await conn.execute(sa.text("PRAGMA table_info(classifier_config)"))
    existing = {row[1] for row in info.fetchall()}
    for column in obsolete_columns:
        if column in existing:
            await conn.execute(sa.text(
                f'ALTER TABLE classifier_config DROP COLUMN "{column}"'
            ))


async def get_db():
    """Dependency to get database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
