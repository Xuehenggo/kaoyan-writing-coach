"""
句子库查询工具 —— 按标签、主题、难度检索结构化句子。
用法: python scripts/query_sentences.py [options]
"""
import json, sys, os, re, argparse
from pathlib import Path

SENTENCES_PATH = Path(__file__).parent.parent / "data" / "sentences.json"

# 章节标签 → 训练环节映射表
CHAPTER_TO_STAGE = {
    # 现象描述
    "2.1.1图画引入句DIY": "现象描述",
    "2.1.2主要人物描述DIY": "现象描述",
    "2.1.3图表引入句DIY": "现象描述",
    "2.1.4数据描述DIY": "现象描述",
    "2.1.5文字段落总结DIY": "现象描述",
    "2.1.6其他DIY": "现象描述",
    "2.1.7首段模板": "现象描述",
    "2.1.8真题解读": "现象描述",
    "1.4.2首段构思": "现象描述",
    # 原因分析
    "2.2.1主题句DIY": "原因分析",
    "2.2第二段": "原因分析",
    "2.2.4第二段模板": "原因分析",
    "2.2.5真题解读": "原因分析",
    "2.2.2逻辑衔接词DIY": "原因分析",
    # 影响论述
    "2.2.3论点DIY": "影响论述",
    "2.3.1点题句DIY": "影响论述",
    # 对策建议
    "2.3.2行动建议句DIY": "对策建议",
    "2.3.4第三段模板": "对策建议",
    "2.3.5真题解读": "对策建议",
    "2.3第三段": "对策建议",
    # 价值升华
    "2.3.3升华展望句DIY": "价值升华",
    # 模板库 (按类型)
    "3.1图画类": "现象描述",
    "3.2文字类": "现象描述",
    "3.3图表类": "现象描述",
    "3.3.1动态图": "现象描述",
    "3.3.2静态图": "现象描述",
    "3.4混合类": "现象描述",
    "3. 模板库": "现象描述",
    # 真题范文 (全覆盖)
    "4. 真题范文精析": "综合",
}

# 主题关键词映射
THEME_KEYWORDS = {
    "T1": ["传统文化", "文化", "传统", "书法", "戏曲", "汉语", "龙舟", "节日", "民俗", "传承", "heritage", "tradition", "culture", "dragon"],
    "T2": ["科技", "网络", "互联网", "人工智能", "AI", "手机", "信息", "数字", "technology", "internet", "digital", "online"],
    "T3": ["教育", "学习", "心理", "成长", "思维", "批判", "选课", "读书", "知识", "education", "learn", "student", "campus"],
    "T4": ["环境", "低碳", "生态", "能源", "绿色", "环保", "污染", "environment", "green", "pollution", "carbon"],
    "T5": ["奋斗", "责任", "理想", "坚持", "乐观", "自信", "独立", "合作", "选择", "persist", "optimis", "confiden", "cooperation", "choice"],
    "T6": ["社会", "老龄化", "志愿", "诚信", "公平", "公共", "社区", "social", "public", "community", "trust"],
}


def load_sentences():
    with open(SENTENCES_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def add_stage(sentence):
    """为句子添加训练环节标签"""
    ch = sentence.get("chapter_tag", "")
    sentence["training_stage"] = CHAPTER_TO_STAGE.get(ch, "未分类")
    return sentence


def query(chapter_tag=None, training_stage=None, theme=None, year=None,
          source=None, min_words=None, max_words=None,
          limit=10, randomize=True, exclude_ids=None):
    """
    查询句子库。

    参数:
        chapter_tag:     章节标签 (精确或模糊匹配)
        training_stage:  训练环节 (现象描述/原因分析/影响论述/对策建议/价值升华)
        theme:           主题 T1-T6 或关键词
        year:            年份 (如 "2023")
        source:          来源类型 (table/inline/template/diy_fragment/paragraph_pair)
        min_words:       最少英文词数
        max_words:       最多英文词数
        limit:           返回上限
        randomize:       是否随机排序
        exclude_ids:     排除的句子 ID 列表
    """
    sentences = load_sentences()
    exclude_ids = set(exclude_ids or [])

    # 添加训练环节
    sentences = [add_stage(s) for s in sentences]

    results = []
    for s in sentences:
        if s["id"] in exclude_ids:
            continue

        # 章节标签匹配
        if chapter_tag:
            if chapter_tag not in s.get("chapter_tag", ""):
                continue

        # 训练环节匹配
        if training_stage:
            if s.get("training_stage", "") != training_stage:
                continue

        # 年份匹配
        if year:
            if s.get("year_tag") != year:
                continue

        # 来源匹配
        if source:
            if s.get("source_location") != source:
                continue

        # 主题匹配
        if theme:
            matched = False
            # 如果是 T1-T6
            if theme in THEME_KEYWORDS:
                keywords = THEME_KEYWORDS[theme]
            else:
                keywords = [theme]

            text = (s.get("clean_sentence", "") + " " + s.get("translation", "")).lower()
            for kw in keywords:
                if kw.lower() in text:
                    matched = True
                    break
            if not matched:
                continue

        # 词数过滤
        en = s.get("clean_sentence", "")
        wc = len(re.findall(r'[a-zA-Z]{2,}', en))
        if min_words is not None and wc < min_words:
            continue
        if max_words is not None and wc > max_words:
            continue

        results.append(s)

    # 排序：优先无 note 标记的（高质量），再按句子长度
    results.sort(key=lambda s: (
        1 if "待确认" in s.get("note", "") else 0,
        -len(s.get("clean_sentence", ""))
    ))

    if randomize and len(results) > limit:
        import random
        # 从前 2*limit 中随机选
        pool = results[:min(2 * limit, len(results))]
        random.shuffle(pool)
        results = pool[:limit]
    else:
        results = results[:limit]

    return results


def query_by_tags(tags, limit=10, exclude_ids=None):
    """便捷接口：传入标签字典查询"""
    return query(
        chapter_tag=tags.get("chapter_tag"),
        training_stage=tags.get("training_stage"),
        theme=tags.get("theme"),
        year=tags.get("year"),
        source=tags.get("source"),
        min_words=tags.get("min_words"),
        max_words=tags.get("max_words"),
        limit=limit,
        exclude_ids=exclude_ids,
    )


# ── CLI ─────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="句子库查询工具")
    parser.add_argument("--chapter", "-c", help="章节标签")
    parser.add_argument("--stage", "-s", help="训练环节 (现象描述/原因分析/影响论述/对策建议/价值升华)")
    parser.add_argument("--theme", "-t", help="主题 T1-T6 或关键词")
    parser.add_argument("--year", "-y", help="年份")
    parser.add_argument("--source", help="来源类型")
    parser.add_argument("--min-words", type=int, help="最少词数")
    parser.add_argument("--max-words", type=int, help="最多词数")
    parser.add_argument("--limit", "-n", type=int, default=5, help="返回数量 (默认 5)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--tags", help="JSON 格式标签查询")

    args = parser.parse_args()

    if args.tags:
        import json as j
        tags = j.loads(args.tags)
        results = query_by_tags(tags, limit=args.limit)
    else:
        results = query(
            chapter_tag=args.chapter,
            training_stage=args.stage,
            theme=args.theme,
            year=args.year,
            source=args.source,
            min_words=args.min_words,
            max_words=args.max_words,
            limit=args.limit,
        )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_readable(results)


def _print_readable(results):
    """人类可读输出"""
    if not results:
        print("(无匹配结果)")
        return

    print(f"共 {len(results)} 条:\n")
    for i, s in enumerate(results, 1):
        print(f"[{i}] {s['id']} | 环节:{s.get('training_stage','?')} | 章节:{s['chapter_tag'][:30]}")
        print(f"    EN: {s['clean_sentence'][:120]}")
        cn = s.get('translation', '')
        if cn:
            print(f"    CN: {cn[:80]}")
        if s.get('year_tag'):
            print(f"    年份: {s['year_tag']}")
        if s.get('note'):
            print(f"    [!] {s['note'][:60]}")
        print()


if __name__ == "__main__":
    main()
