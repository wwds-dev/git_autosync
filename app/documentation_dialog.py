"""In-app viewer for README.md — keeps documentation inside the app instead
of shelling out to whatever's registered as the default .md handler
(Pages, VS Code, etc.), which is jarring and inconsistent across machines.

The page has a contents sidebar (click a heading to jump to it) and a search
box (⌘F): every match is highlighted, Enter / Shift+Enter step through them,
Esc clears. The styling is applied to the parsed document rather than via CSS,
because Qt's Markdown importer ignores a default stylesheet."""
from PySide6.QtCore import Qt
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat,
    QTextCursor, QTextDocument, QTextLength, QTextTable,
)
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QSplitter, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)

INK = "#1D1D1F"
MUTED = "#6E6E73"
ACCENT = "#0A64D6"
RULE = "#E3E5E8"
CODE_BG = "#F2F3F5"
HIT_BG = "#FFE58A"
CURRENT_BG = "#FF9F1A"

# Heading level -> (point size, weight, top margin, bottom margin)
_HEADINGS = {
    1: (24, QFont.Bold, 4, 12),
    2: (18, QFont.Bold, 26, 8),
    3: (14, QFont.DemiBold, 18, 6),
    4: (13, QFont.DemiBold, 14, 4),
}


def style_document(doc: QTextDocument) -> None:
    """Give a Markdown-imported document a readable, consistent look."""
    cur = QTextCursor(doc)
    block = doc.begin()
    while block.isValid():
        fmt = block.blockFormat()
        level = fmt.headingLevel()
        cur.setPosition(block.position())
        cur.setPosition(block.position() + max(block.length() - 1, 0),
                        QTextCursor.KeepAnchor)

        if level:
            size, weight, top, bottom = _HEADINGS.get(level, _HEADINGS[4])
            cf = QTextCharFormat()
            cf.setFontPointSize(size)
            cf.setFontWeight(weight)
            cf.setForeground(QColor(INK))
            cur.mergeCharFormat(cf)
            fmt.setTopMargin(top)
            fmt.setBottomMargin(bottom)
            cur.setBlockFormat(fmt)
        elif fmt.nonBreakableLines() and block.length() > 1:
            # Code block: shaded, padded, and *wrapped* — a long command used
            # to run off the right edge and could not be read or copied.
            fmt.setNonBreakableLines(False)
            fmt.setBackground(QColor(CODE_BG))
            fmt.setLeftMargin(10)
            fmt.setRightMargin(10)
            cur.setBlockFormat(fmt)
        else:
            fmt.setLineHeight(140, QTextBlockFormat.ProportionalHeight.value)
            if not fmt.topMargin():
                fmt.setTopMargin(2)
            fmt.setBottomMargin(max(fmt.bottomMargin(), 6))
            cur.setBlockFormat(fmt)

        # Inline code and links
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid():
                cf = frag.charFormat()
                span = QTextCursor(doc)
                span.setPosition(frag.position())
                span.setPosition(frag.position() + frag.length(),
                                 QTextCursor.KeepAnchor)
                if cf.isAnchor():
                    patch = QTextCharFormat()
                    patch.setForeground(QColor(ACCENT))
                    span.mergeCharFormat(patch)
                elif cf.fontFixedPitch() and not level:
                    patch = QTextCharFormat()
                    patch.setBackground(QColor(CODE_BG))
                    patch.setForeground(QColor("#B3261E"))
                    span.mergeCharFormat(patch)
            it += 1
        block = block.next()

    for frame in doc.rootFrame().childFrames():
        if isinstance(frame, QTextTable):
            _style_table(frame)


def _style_table(table: QTextTable) -> None:
    f = table.format()
    f.setBorder(1)
    f.setBorderBrush(QColor(RULE))
    f.setCellPadding(7)
    f.setCellSpacing(0)
    f.setWidth(QTextLength(QTextLength.PercentageLength, 100))
    f.setTopMargin(8)
    f.setBottomMargin(12)
    table.setFormat(f)
    for col in range(table.columns()):
        head = table.cellAt(0, col)
        cf = head.format()
        cf.setBackground(QColor("#EEF2F7"))
        cf.setFontWeight(QFont.Bold)
        head.setFormat(cf)




def headings(doc: QTextDocument) -> list[tuple[int, str, int]]:
    """(level, text, position) for every heading, in document order."""
    out = []
    block = doc.begin()
    while block.isValid():
        level = block.blockFormat().headingLevel()
        text = block.text().strip()
        if level and text:
            out.append((level, text, block.position()))
        block = block.next()
    return out


def find_all(doc: QTextDocument, needle: str) -> list[QTextCursor]:
    """Every case-insensitive occurrence of `needle`, in document order."""
    needle = needle.strip()
    if not needle:
        return []
    hits, cur = [], QTextCursor(doc)
    while True:
        cur = doc.find(needle, cur)
        if cur.isNull():
            return hits
        hits.append(QTextCursor(cur))


class DocumentationDialog(QDialog):
    def __init__(self, parent, markdown_text: str, updated: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Documentation")
        self.resize(1020, 760)
        self.setStyleSheet("QDialog { background: #FFFFFF; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # --- top bar: search + staleness badge -------------------------------
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the documentation   ⌘F")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(280)
        self.search.setStyleSheet(
            f"QLineEdit {{ background:#F5F6F8; color:{INK}; border:1px solid {RULE};"
            f" border-radius:8px; padding:6px 10px; font-size:13px; }}"
            f"QLineEdit:focus {{ border:1px solid {ACCENT}; background:#FFFFFF; }}")
        top.addWidget(self.search, stretch=1)

        small = (f"QPushButton {{ background:#F5F6F8; color:{INK}; border:1px solid {RULE};"
                 f" border-radius:6px; padding:4px 10px; }}"
                 f"QPushButton:disabled {{ color:#B0B0B5; }}"
                 f"QPushButton:hover:!disabled {{ background:#EAECEF; }}")
        self.prev_btn = QPushButton("↑")
        self.next_btn = QPushButton("↓")
        for b, tip in ((self.prev_btn, "Previous match (Shift+Enter)"),
                       (self.next_btn, "Next match (Enter)")):
            b.setToolTip(tip)
            b.setFixedWidth(34)
            b.setStyleSheet(small)
            top.addWidget(b)

        self.count_label = QLabel("")
        self.count_label.setMinimumWidth(78)
        self.count_label.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        top.addWidget(self.count_label)

        # Docs age badly and silently. Say when this text last changed so a
        # stale page is obvious rather than trusted.
        self.updated_label = QLabel(
            f"Last updated: {updated}" if updated else "Last updated: unknown")
        self.updated_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.updated_label.setStyleSheet(f"color:{MUTED}; font-size:11px; padding-left:12px;")
        top.addWidget(self.updated_label)
        root.addLayout(top)

        # --- body: contents + page -------------------------------------------
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        base = QFont(self.browser.font())
        base.setPointSize(13)
        self.browser.setFont(base)
        self.browser.document().setDocumentMargin(26)
        self.browser.setMarkdown(markdown_text)
        style_document(self.browser.document())
        self.browser.setStyleSheet(
            f"QTextBrowser {{ background:#FFFFFF; color:{INK}; border:1px solid {RULE};"
            f" border-radius:8px; selection-background-color:#BBD7FB;"
            f" selection-color:{INK}; }}")

        self.toc = QListWidget()
        self.toc.setFixedWidth(250)
        self.toc.setFocusPolicy(Qt.NoFocus)
        self.toc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.toc.setTextElideMode(Qt.ElideRight)
        self.toc.setStyleSheet(
            f"QListWidget {{ background:#F7F8FA; color:{INK}; border:1px solid {RULE};"
            f" border-radius:8px; padding:6px; font-size:12px; outline:0; }}"
            f"QListWidget::item {{ padding:4px 6px; border-radius:5px; }}"
            f"QListWidget::item:hover {{ background:#E9ECF1; }}"
            f"QListWidget::item:selected {{ background:#DCE9FB; color:{INK}; }}")
        self._fill_toc()
        self.toc.itemClicked.connect(self._jump)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.toc)
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)
        root.addWidget(split, stretch=1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.reject)
        root.addWidget(buttons)

        # --- search wiring ----------------------------------------------------
        self._hits: list[QTextCursor] = []
        self._cur = -1
        self.search.textChanged.connect(self._search)
        self.search.returnPressed.connect(self.next_match)
        self.next_btn.clicked.connect(self.next_match)
        self.prev_btn.clicked.connect(self.prev_match)
        QShortcut(QKeySequence.Find, self, activated=self._focus_search)
        QShortcut(QKeySequence("Shift+Return"), self.search,
                  activated=self.prev_match)
        QShortcut(QKeySequence(Qt.Key_Escape), self.search,
                  activated=self._escape)
        self._update_buttons()

    # -- contents ----------------------------------------------------------------
    def _fill_toc(self) -> None:
        for level, text, pos in headings(self.browser.document()):
            if level > 3:
                continue
            item = QListWidgetItem(("    " * (level - 1)) + text)
            item.setData(Qt.UserRole, pos)
            if level == 1:
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            elif level == 3:
                item.setForeground(QColor(MUTED))
            self.toc.addItem(item)

    def _jump(self, item: QListWidgetItem) -> None:
        self._show(item.data(Qt.UserRole), top=True)

    def _show(self, pos: int, top: bool = False) -> None:
        cur = QTextCursor(self.browser.document())
        cur.setPosition(pos)
        self.browser.setTextCursor(cur)
        if top:
            bar = self.browser.verticalScrollBar()
            bar.setValue(bar.value() + self.browser.cursorRect(cur).top() - 8)
        else:
            self.browser.ensureCursorVisible()

    # -- search ------------------------------------------------------------------
    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _escape(self) -> None:
        if self.search.text():
            self.search.clear()
        else:
            self.browser.setFocus()

    def _search(self, text: str) -> None:
        self._hits = find_all(self.browser.document(), text)
        self._cur = 0 if self._hits else -1
        self._paint()
        if self._hits:
            self._show(self._hits[0].selectionStart())
        self._update_buttons()

    def next_match(self) -> None:
        self._step(1)

    def prev_match(self) -> None:
        self._step(-1)

    def _step(self, d: int) -> None:
        if not self._hits:
            return
        self._cur = (self._cur + d) % len(self._hits)
        self._paint()
        self._show(self._hits[self._cur].selectionStart())
        self._update_buttons()

    def _paint(self) -> None:
        sels = []
        for i, c in enumerate(self._hits):
            s = QTextEdit.ExtraSelection()
            s.cursor = c
            s.format.setBackground(QColor(CURRENT_BG if i == self._cur else HIT_BG))
            sels.append(s)
        self.browser.setExtraSelections(sels)

    def _update_buttons(self) -> None:
        n = len(self._hits)
        self.prev_btn.setEnabled(n > 1)
        self.next_btn.setEnabled(n > 1)
        if not self.search.text().strip():
            self.count_label.setText("")
        elif n == 0:
            self.count_label.setText("No matches")
        else:
            self.count_label.setText(f"{self._cur + 1} of {n}")
