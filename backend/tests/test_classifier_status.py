import unittest

from app.routers.classifier import (
    _auto_classify_state,
    get_auto_classify_status,
)


class _FakeDb:
    def __init__(self, review_count):
        self.review_count = review_count

    async def scalar(self, _statement):
        return self.review_count


class AutoClassifyStatusTest(unittest.IsolatedAsyncioTestCase):
    async def test_live_review_queue_count_is_independent_from_run_counter(self):
        previous_reviewed = _auto_classify_state["reviewed"]
        try:
            _auto_classify_state["reviewed"] = 9
            status = await get_auto_classify_status(_FakeDb(review_count=2))
        finally:
            _auto_classify_state["reviewed"] = previous_reviewed

        self.assertEqual(status["reviewed"], 9)
        self.assertEqual(status["review_queue_count"], 2)


if __name__ == "__main__":
    unittest.main()
