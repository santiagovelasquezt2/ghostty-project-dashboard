import unittest
from rich.console import Console
from dashboard.panels import diff_text, totals_text, GREEN, RED, BLUE, NEUTRAL

class HighlightTests(unittest.TestCase):
    def test_languages_preserve_literal_diff_and_apply_theme(self):
        for path, code in [('a.ts', 'const name: string = "test";'), ('a.tsx', 'const el = <div title="test" />;'), ('a.js','const name = "test";'), ('a.jsx','const el = <div />;'), ('a.html','<div class="test">Hi</div>'), ('a.css','body { color: red; }')]:
            raw = '@@ -1 +1 @@\n-old\n+' + code + '\n'
            view = diff_text(raw, path)
            self.assertNotIn("@@", view.plain)
            self.assertIn("Before: lines", view.plain)
            colors = {seg.style.color.get_truecolor().hex for seg in Console().render(view) if seg.style and seg.style.color}
            self.assertTrue(colors & {'#c586c0','#569cd6','#9cdcfe'}, path)
            self.assertIn(GREEN,colors)
            self.assertIn(RED,colors)

    def test_multifile_deleted_and_comment_continuation(self):
        raw = 'diff --git a/a.ts b/a.ts\n--- a/a.ts\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-/* comment\n-still comment */\ndiff --git a/b.html b/b.html\n--- /dev/null\n+++ b/b.html\n@@ -0,0 +1 @@\n+<div>test</div>\n'
        view = diff_text(raw)
        self.assertNotIn("@@", view.plain)
        self.assertIn("Before: lines", view.plain)
        styles = [span.style for span in view.spans]
        self.assertIn('#6a9955', styles)
        self.assertIn('#569cd6', styles)

    def test_legend_uses_file_colors(self):
        view = totals_text([])
        for word,color in [('Added',GREEN),('Modified',BLUE),('Deleted',RED),('Unchanged',NEUTRAL)]:
            self.assertTrue(any(view.plain[s.start:s.end] == word and s.style == color for s in view.spans))

class ReviewDiffTests(unittest.TestCase):
    def test_removed_and_added_lines_have_distinct_backgrounds_and_context(self):
        raw='@@ -1,3 +1,3 @@\n const before = 1;\n-const value = 2;\n+const value = 3;\n const after = 4;\n'
        view=diff_text(raw,'app.ts')
        self.assertNotIn("@@",view.plain)
        self.assertIn("Before: lines 1–3 → After: lines 1–3",view.plain)
        removed=[view.plain[s.start:s.end] for s in view.spans if s.style=='on #381b22']
        added=[view.plain[s.start:s.end] for s in view.spans if s.style=='on #123022']
        self.assertEqual(removed,['-const value = 2;'])
        self.assertEqual(added,['+const value = 3;'])
