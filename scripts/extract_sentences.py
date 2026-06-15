"""
V3: 修复句子合并、book_name缺失、章节追踪、EN/CN分离质量
"""
import json, re, os
from bs4 import BeautifulSoup
from collections import OrderedDict

BASE = r"D:/kaoyan-writing-coach/references"
OUTPUT = r"D:/kaoyan-writing-coach/data/sentences.json"
TAGS_OUTPUT = r"D:/kaoyan-writing-coach/data/chapter_tags.json"
REPORT = r"D:/kaoyan-writing-coach/data/extraction_report.md"

FILES = [
    ("template-methodology.md", "备考方法论"),
    ("template-diy.md", "内容三段论"),
    ("template-library.md", "模板库"),
    ("template-model-essays.md", "真题范文精析"),
]


# ═══════════════════════════════════ UTILS

def clean(text):
    if not text:
        return ""
    text = re.sub(r'\$\s*\\?textcircled\{[^}]*\}\s*\$', '', text)
    text = re.sub(r'\$\s*\\rightarrow\s*\$', '→', text)
    text = re.sub(r'\$\s*[^$]*\s*\$', '', text)
    text = re.sub(r'<!--.*?-->', '', text)
    text = re.sub(r'&#x27;', "'", text)
    text = re.sub(r'&gt;', '>', text)
    text = re.sub(r'&lt;', '<', text)
    text = re.sub(r'&amp;', '&', text)
    # Normalize whitespace but preserve paragraphs
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def en_words(text):
    return len(re.findall(r'[a-zA-Z]{2,}', text or ""))


def cn_chars(text):
    return len(re.findall(r'[一-鿿]', text or ""))


def is_en(text, min_w=5):
    return en_words(text) >= min_w


def is_cn(text, min_c=5):
    return cn_chars(text) >= min_c


def is_noise(text):
    t = (text or "").strip()
    if not t or len(t) < 2:
        return True
    if re.match(r'^\d{1,3}$', t):
        return True
    if t in ['.', '..', '...', '-', '—']:
        return True
    return False


def extract_year(text):
    m = re.search(r'(20[012]\d)', str(text))
    return m.group(1) if m else None


def normalize_header(h):
    h = clean(h).strip()
    h = re.sub(r'^[>\s]+', '', h)
    return h


def is_real_section(title):
    """判断是否为真正的章节标题（而非噪音）"""
    t = title.strip()
    if re.match(r'^(Directions|解读|翻译|思路|模板套用|主题词|A\.|B\.|C\.|来看一下)', t):
        return False
    if re.match(r'^\d+\)', t):
        return False
    if re.search(r'真题例文|详见', t):
        return False
    if len(t) < 3:
        return False
    return True


# ═══════════════════════════════════ SPLIT MEGA SENTENCES

def split_mega_entry(en_text, cn_text=""):
    """将合并的多个句子拆分为独立条目"""
    results = []

    # 按 ## 分割（OCR 将多段合并时留下的标记）
    parts = re.split(r'\s*##\s+', en_text)
    cn_parts = re.split(r'\s*##\s+', cn_text) if cn_text else []

    if len(parts) == 1:
        # 无 ## 分隔，尝试按多句拆分
        sents = re.split(r'(?<=[.?!])\s+(?=[A-Z])', en_text)
        if len(sents) <= 1:
            return [(en_text, cn_text)]
        # 多个句子但无对应翻译，保持合并
        return [(en_text, cn_text)]

    # 对齐 en 和 cn 部分
    pairs = []
    for i, en_part in enumerate(parts):
        en_part = en_part.strip()
        if not is_en(en_part, min_w=3):
            continue
        cn_part = cn_parts[i].strip() if i < len(cn_parts) else ""
        if cn_part and not is_cn(cn_part, min_c=3):
            cn_part = ""
        pairs.append((en_part, cn_part))

    return pairs if pairs else [(en_text, cn_text)]


# ═══════════════════════════════════ BETTER EN/CN SPLIT

def split_en_cn_line(text):
    """从混合行精确分离 EN 和 CN"""
    text = clean(text)
    # 找最后一个英文句号/问号/感叹号后的中文
    # 模式: ...English sentence. Chinese text...
    m = re.match(
        r'^([A-Z][\x00-\x7f\s\-\',;:!?.()\"\[\]{}#@&/\d\*\+—––—]{15,300}?[.?!])'
        r'\s*([一-鿿][\s\S]{5,})$',
        text
    )
    if m:
        en = m.group(1).strip()
        cn = m.group(2).strip()
        if is_en(en, 4) and is_cn(cn, 3):
            return en, cn

    # Fallback: 找中英交界
    for sep in ['. ', '? ', '! ']:
        idx = text.rfind(sep)
        if idx > 20:
            en_part = text[:idx + 1]
            cn_part = text[idx + 2:]
            if is_en(en_part, 4) and is_cn(cn_part, 3):
                return en_part.strip(), cn_part.strip()

    return text, ""


# ═══════════════════════════════════ CHAPTER TRACKER

class ChapterTracker:
    def __init__(self, book_name):
        self.book_name = book_name
        self.chapter = "章节未明确"
        self.section = ""
        # 为表格抽取维护位置索引
        self.position_chapters = []  # (char_pos, chapter, section)

    def scan_all(self, content):
        """预扫描全文，建立位置→章节映射"""
        for m in re.finditer(r'^##\s+(.+?)$', content, re.MULTILINE):
            title = normalize_header(m.group(1))
            if not is_real_section(title):
                continue
            if re.match(r'^[1-4][\.\s]', title):
                self.chapter = title
                self.section = title
            else:
                self.section = title
            self.position_chapters.append((m.start(), self.chapter, self.section))

    def at_position(self, pos):
        """返回某字符位置的章节上下文"""
        ch, sec = "章节未明确", ""
        for p, c, s in self.position_chapters:
            if p <= pos:
                ch, sec = c, s
            else:
                break
        return ch, sec

    def scan_line(self, line):
        """逐行更新章节"""
        m = re.match(r'^##\s+(.+?)$', line.strip())
        if not m:
            return
        title = normalize_header(m.group(1))
        if not is_real_section(title):
            return
        if re.match(r'^[1-4][\.\s]', title):
            self.chapter = title
            self.section = title
        else:
            self.section = title


# ═══════════════════════════════════ EXTRACTORS

def extract_tables(content, tracker):
    """从 HTML table 抽取（带位置追踪）"""
    soup = BeautifulSoup(content, 'lxml')
    results = []
    for table in soup.find_all('table'):
        # 获取 table 在原文中的位置
        table_str = str(table)
        pos = content.find(table_str[:200])
        if pos < 0:
            pos = 0
        ch, sec = tracker.at_position(pos)

        for tr in table.find_all('tr'):
            tds = tr.find_all('td')
            if len(tds) < 2:
                continue

            cell_texts = [td.get_text(strip=True) for td in tds]

            # 尝试识别 year | EN | CN 或 EN | CN 结构
            for i in range(len(cell_texts)):
                cell = cell_texts[i]
                if not is_en(cell, min_w=4):
                    continue

                # 找中文翻译
                cn = ""
                for j, other in enumerate(cell_texts):
                    if j != i and is_cn(other):
                        cn = other
                        break

                # 或同 cell 内分离
                en_part = cell
                if not cn:
                    en_part, cn = split_en_cn_line(cell)

                year = None
                # 年份通常在第一个 td 或当前 td
                if len(cell_texts) > 0:
                    year = extract_year(cell_texts[0])
                if not year:
                    year = extract_year(cell)

                # 拆分合并句子
                sub_pairs = split_mega_entry(en_part, cn)
                for sub_en, sub_cn in sub_pairs:
                    if is_en(sub_en, min_w=4):
                        results.append(OrderedDict([
                            ("sentence_en", sub_en),
                            ("sentence_cn", sub_cn),
                            ("chapter_tag", ch),
                            ("section_title", sec),
                            ("source_location", "table"),
                            ("year_tag", year),
                            ("note", ""),
                        ]))
    return results


def extract_inline(text, tracker, book_name):
    """内联 EN+CN"""
    # 先按 ## 拆开
    blocks = re.split(r'\s*##\s+', text)
    results = []
    for block in blocks:
        block = block.strip()
        if '<table' in block or not block:
            continue
        en, cn = split_en_cn_line(block)
        if cn and is_en(en, min_w=5):
            # 检查 translation 里有没有混入英文
            if en_words(cn) > en_words(en) * 0.3:
                # translation 英文太多，可能分错了
                en, cn = block, ""
            sub_pairs = split_mega_entry(en, cn)
            for sub_en, sub_cn in sub_pairs:
                if is_en(sub_en, min_w=5):
                    results.append(OrderedDict([
                        ("sentence_en", sub_en),
                        ("sentence_cn", sub_cn),
                        ("chapter_tag", tracker.chapter),
                        ("section_title", tracker.section),
                        ("source_location", "inline"),
                        ("year_tag", extract_year(sub_en)),
                        ("note", ""),
                    ]))
    return results


def extract_templates(text, tracker):
    """模板句"""
    if '<table' in text:
        return []
    text = clean(text)
    results = []
    for s in re.split(r'(?<=[.?!])\s+', text):
        s = s.strip()
        if is_en(s, min_w=8) and re.search(r'主题词|某[人物]|xxx|XXX|某物|sb\.|sth\.', s):
            results.append(OrderedDict([
                ("sentence_en", s),
                ("sentence_cn", ""),
                ("chapter_tag", tracker.chapter),
                ("section_title", tracker.section),
                ("source_location", "template"),
                ("year_tag", None),
                ("note", "含占位符，翻译见相邻段落"),
            ]))
    return results


def extract_diy_fragments(text, tracker):
    """DIY 短语 (4-8词)"""
    if '<table' in text:
        return []
    text = clean(text)
    results = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        m = re.match(
            r'([A-Z][a-z\s,\-;:]{12,100})'
            r'[.?!]?\s*'
            r'([一-鿿][一-鿿，。！？；：\s\d\w]{3,100})',
            line
        )
        if m:
            en = m.group(1).strip()
            cn = m.group(2).strip()
            wc = en_words(en)
            if 4 <= wc <= 8 and is_cn(cn, min_c=3) and re.match(r'^[A-Z]', en):
                if re.match(r'^[A-Z][a-z\s,\-;:]*$', en) and len(en.split()) >= 4:
                    results.append(OrderedDict([
                        ("sentence_en", en),
                        ("sentence_cn", cn),
                        ("chapter_tag", tracker.chapter),
                        ("section_title", tracker.section),
                        ("source_location", "diy_fragment"),
                        ("year_tag", None),
                        ("note", ""),
                    ]))
    return results


def extract_paragraph_pairs(paragraphs, tracker):
    """EN 段落 + CN 段落"""
    results = []
    for i in range(len(paragraphs) - 1):
        p1 = paragraphs[i].strip()
        p2 = paragraphs[i + 1].strip()
        if is_noise(p1) or is_noise(p2):
            continue
        if '<table' in p1 or '<table' in p2 or '##' in p1.split('\n')[0]:
            continue

        w1, w2 = en_words(p1), en_words(p2)
        c1, c2 = cn_chars(p1), cn_chars(p2)

        # EN→CN 模式
        if w1 >= 20 and c1 < w1 * 0.3 and c2 >= 20:
            results.append(OrderedDict([
                ("sentence_en", p1),
                ("sentence_cn", p2),
                ("chapter_tag", tracker.chapter),
                ("section_title", tracker.section),
                ("source_location", "paragraph_pair"),
                ("year_tag", extract_year(p1) or extract_year(p2)),
                ("note", ""),
            ]))
    return results


# ═══════════════════════════════════ MAIN

def process_file(filepath, book_name):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    tracker = ChapterTracker(book_name)
    tracker.scan_all(content)

    # 重置用于逐行扫描
    tracker2 = ChapterTracker(book_name)
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', content) if not is_noise(p.strip())]

    all_results = []

    # 1. Tables（使用位置索引）
    table_results = extract_tables(content, tracker)
    all_results.extend(table_results)

    # 2. 段落级抽取
    for p_text in paragraphs:
        tracker2.scan_line(p_text.split('\n')[0] if '\n' in p_text else p_text)

        # 内联
        all_results.extend(extract_inline(p_text, tracker2, book_name))
        # DIY 片段
        all_results.extend(extract_diy_fragments(p_text, tracker2))
        # 模板
        all_results.extend(extract_templates(p_text, tracker2))

    # 3. 段落配对
    all_results.extend(extract_paragraph_pairs(paragraphs, tracker2))

    # 去重
    seen = set()
    unique = []
    for r in all_results:
        key = r['sentence_en'][:120]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique


def extract_tag_hierarchy():
    """提取章节标签层级"""
    all_tags = OrderedDict()
    for fname, book_name in FILES:
        fpath = os.path.join(BASE, fname)
        with open(fpath, 'r', encoding='utf-8') as f:
            content = f.read()
        headers = re.findall(r'^##\s+(.+?)$', content, re.MULTILINE)
        tags = []
        for h in headers:
            h = normalize_header(h)
            if is_real_section(h):
                tags.append(h)
        all_tags[f"{book_name}"] = tags
    return all_tags


# ═══════════════════════════════════ RUN

if __name__ == '__main__':
    tag_hierarchy = extract_tag_hierarchy()

    all_sentences = []
    stats = {}

    for fname, book_name in FILES:
        fpath = os.path.join(BASE, fname)
        results = process_file(fpath, book_name)
        stats[book_name] = len(results)
        # 填充 book_name
        for r in results:
            r["book_name"] = book_name
        all_sentences.extend(results)

    # 分配 ID + 标准化字段
    final = []
    for i, s in enumerate(all_sentences):
        en_text = s.get("sentence_en", "")
        cn_text = s.get("sentence_cn", "")
        ch = s.get("chapter_tag", "章节未明确")

        # 最终清理
        en_text = re.sub(r'\s*##\s*', ' ', en_text).strip()
        cn_text = re.sub(r'\s*##\s*', ' ', cn_text).strip()

        # 判断置信度
        note = s.get("note", "")
        if ch == "章节未明确":
            note = (note + "；章节未明确").strip("；")

        final.append(OrderedDict([
            ("id", f"KYC-{i+1:04d}"),
            ("book_name", s.get("book_name", "")),
            ("chapter_tag", ch),
            ("section_title", s.get("section_title", "")),
            ("clean_sentence", en_text),
            ("translation", cn_text),
            ("original_text", s.get("sentence_en", "")),
            ("source_location", s.get("source_location", "")),
            ("year_tag", s.get("year_tag")),
            ("note", note),
        ]))

    # 保存
    with open(OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    with open(TAGS_OUTPUT, 'w', encoding='utf-8') as f:
        json.dump(tag_hierarchy, f, ensure_ascii=False, indent=2)

    # 统计
    chapter_dist = OrderedDict()
    for s in final:
        ch = s["chapter_tag"]
        chapter_dist[ch] = chapter_dist.get(ch, 0) + 1

    src_dist = OrderedDict()
    for s in final:
        src = s["source_location"]
        src_dist[src] = src_dist.get(src, 0) + 1

    # 报告
    report = [
        "# 句子提取报告 (V3)",
        "",
        "## 文件统计",
    ]
    for book_name, cnt in stats.items():
        report.append(f"- **{book_name}**: {cnt} 条")
    report.append(f"\n**总计: {len(final)} 条**")

    report.append("\n## 章节分布")
    for ch, cnt in chapter_dist.items():
        report.append(f"- {ch}: {cnt} 条")

    report.append("\n## 来源分布")
    for src, cnt in src_dist.items():
        report.append(f"- {src}: {cnt} 条")

    report.append("\n## 章节标签体系总表")
    for book_name, tags in tag_hierarchy.items():
        report.append(f"\n### {book_name}")
        for t in tags:
            report.append(f"- `{t}`")

    report.append("\n## 清洗规则和章节归类规则")
    report.append("""
### 清洗规则
1. **HTML表格抽取**: BeautifulSoup 解析 `<table>`，识别 year|EN|CN 三元组
2. **段落配对**: ≥20英文词段落 + 相邻≥20中文字段落
3. **内联配对**: 正则分离 EN句.CN翻译 紧凑格式
4. **模板句**: 含"主题词/某人/某物/sb./sth."等占位符
5. **DIY片段**: 4-8英文词 + 中文解释，以大写字母开头
6. **合并拆分**: 检测 `##` OCR粘连标记，拆分多句合并条目

### 噪声过滤
- LaTeX 公式 (`$...$`)、HTML注释 (`<!--...-->`)
- 孤立页码、纯数字、空行
- 非章节标题（Directions、解读、翻译、思路等模板标签）

### 章节归类规则
- 按 `##` Markdown 标题识别章节边界
- 主章节匹配 `^[1-4][.\\s]` 模式
- 每个句子继承其上方最近的有效章节标题
- Table 条目按原文字符位置匹配章节（而非文档末尾）
- 无法匹配的标记为"章节未明确"
""")

    with open(REPORT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report))

    print(f"Done! {len(final)} sentences → {OUTPUT}")
    for book_name, cnt in stats.items():
        print(f"  {book_name}: {cnt}")
