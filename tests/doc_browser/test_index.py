import os
import unittest

from common import SVG1, SVG3, Env, page

KIT_MIN = '<meta name="doc-kit-kind" content="결정">'


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        import doc_index
        self.di = doc_index

    def tearDown(self):
        self.env.close()

    def refresh(self):
        ix = self.di.Index()
        ix.refresh()
        return ix

    def test_collect_newest_first_and_hub_names(self):
        a = self.env.write(self.env.docs, "old.html", page("옛 문서"))
        b = self.env.write(self.env.ws, "new/new.html", page("새 문서"))
        os.utime(a, (1000, 1000))
        os.utime(b, (2000, 2000))
        docs = self.refresh().snapshot()
        self.assertEqual([d.title for d in docs], ["새 문서", "옛 문서"])
        self.assertEqual([d.hub for d in docs], ["vfx", "정리기록"])

    def test_diagram_flags_reuse_checker(self):
        self.env.write(self.env.docs, "with.html", page("있음", KIT_MIN + SVG3))
        self.env.write(self.env.docs, "icon.html", page("아이콘만", KIT_MIN + SVG1))
        self.env.write(self.env.docs, "none.html", page("없음"))
        self.env.write(self.env.docs, "skip.html", page("예외", '<meta name="doc-kit-no-diagram" content="도식 필요 없는 메모">'))
        by = {d.title: d for d in self.refresh().snapshot()}
        self.assertEqual((by["있음"].diagrams, by["있음"].thumb), (1, True))
        self.assertEqual((by["아이콘만"].diagrams, by["아이콘만"].thumb), (0, False))
        self.assertEqual(by["없음"].diagrams, 0)
        self.assertEqual(by["예외"].no_diagram_reason, "도식 필요 없는 메모")

    def test_missing_checker_marks_undecidable(self):
        self.env.write(self.env.docs, "a.html", page("가"))
        os.environ["LRM_DOC_CHECK"] = "/없는/check.py"
        import importlib
        importlib.reload(self.di)
        self.assertIsNone(self.refresh().snapshot()[0].diagrams)

    def test_thumb_is_first_real_diagram_and_script_free(self):
        evil = '<svg><script>alert(1)</script><rect onclick="x()" width="1" height="1"/><rect/><rect/></svg>'
        self.env.write(self.env.docs, "t.html", page("썸네일", SVG1 + evil))
        ix = self.refresh()
        svg = ix.thumb_of(ix.snapshot()[0].id)
        self.assertIn("<rect", svg)
        self.assertNotIn("script", svg.lower())
        self.assertNotIn("onclick", svg)

    def test_unknown_id_has_no_path(self):
        ix = self.refresh()
        self.assertIsNone(ix.path_of("../../etc/passwd"))
        self.assertIsNone(ix.thumb_of("zzz"))

    def test_decision_ids_and_answer_state(self):
        p = self.env.write(self.env.docs, "20261113-plan.html", page("계획"))
        self.env.write(self.env.docs, "other.html", page("무관"))
        self.env.jsonl("slack-sent.jsonl", [
            {"ts": "1.1", "form_kind": "decide", "decision_id": "D-AAA", "options": {"1": "a"}},
            {"ts": "2.2", "form_kind": "decide", "decision_id": "D-BBB", "options": {"1": "a"}},
            {"ts": "3.3", "form_kind": "decide", "decision_id": "D-CCC", "options": {"1": "a"}},
            {"ts": "", "thread_ts": "1.1", "kind": "file", "path": "/stage/ab12-20261113-plan.html"},
            {"ts": "", "thread_ts": "2.2", "kind": "file", "path": p},
            {"ts": "", "thread_ts": "3.3", "kind": "file", "path": "/x/20261113-plan.html"},
        ])
        self.env.jsonl("slack-inbox.jsonl", [
            {"type": "button", "parent_ts": "1.1", "kind": "decide", "decision_id": "D-AAA", "action": "pick", "pick": 1},
            {"type": "reaction", "parent_ts": "3.3", "kind": "decide", "decision_id": "D-CCC", "action": "hold"},
        ])
        by = {d.title: d for d in self.refresh().snapshot()}
        got = {x["id"]: x["state"] for x in by["계획"].decisions}
        self.assertEqual(got, {"D-AAA": "답함", "D-BBB": "대기", "D-CCC": "보류"})
        self.assertEqual(by["무관"].decisions, [])

    def test_public_record_hides_path(self):
        self.env.write(self.env.docs, "a.html", page("가"))
        pub = self.refresh().snapshot()[0].public()
        self.assertNotIn("path", pub)
        self.assertNotIn(self.env.tmp.name, str(pub))

    def test_cache_skips_unchanged_files(self):
        self.env.write(self.env.docs, "a.html", page("가"))
        self.refresh()
        calls = []
        orig = self.di.parse_file
        self.di.parse_file = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
        ix = self.di.Index()
        ix.refresh()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
