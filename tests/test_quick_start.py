import unittest

from software_copyright_agent.quick_start import QuickStartBlocked, QuickStartService


class QuickStartQualityGateTests(unittest.TestCase):
    def test_passed_qa_with_reminders_still_honors_no_warnings_preference(self):
        qa = {"document": {"version": 4},
              "qa_run": {"passed": True, "summary": {"failed_check_count": 0, "warning_count": 2}}}
        with self.assertRaises(QuickStartBlocked) as caught:
            QuickStartService._check_warning_policy(qa, False)
        self.assertEqual(caught.exception.details["document_version"], 4)
        QuickStartService._check_warning_policy(qa, True)
        qa["qa_run"]["summary"]["warning_count"] = 0
        QuickStartService._check_warning_policy(qa, False)

    def test_recovery_prefers_new_candidate_and_never_rechecks_stale_final(self):
        def doc(version, kind, fresh="current", generator=True):
            return {"version": version, "document_kind": kind,
                    "freshness": {"status": fresh}, "integrity": {"status": "verified"},
                    "quality": {"current_generator": generator}}
        old = doc(3, "final_document")
        candidate = doc(4, "formal_candidate")
        self.assertEqual(QuickStartService._current_manual_documents([old, candidate]),
                         (candidate, None))
        self.assertEqual(QuickStartService._current_manual_documents([
            doc(3, "final_document", fresh="outdated"),
            doc(2, "formal_candidate", generator=False)]), (None, None))
        final = doc(5, "final_document")
        self.assertEqual(QuickStartService._current_manual_documents([candidate, final]),
                         (candidate, final))
