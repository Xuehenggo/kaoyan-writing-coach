# 考研英语写作训练教练 (Kaoyan Writing Coach)

一个Skill，融合了考研大纲和英一写作专业资料，为考研英语一考生提供写作与翻译训练的智能教练。

## 功能

- **出题**：基于考研大纲 5500 词，生成大作文和翻译练习题
- **批改**：对作文/翻译进行评分和详细批改
- **错误编码反馈**：系统化分类错误（语法、词汇、句式等），每次聚焦 5 条致命错误
- **深挖→回归**：针对薄弱点进行微靶训练，再回归实战
- **学情追踪**：自动记录学习进度、弱项、错词，数据驱动教学决策

## 适用对象

- 考研英语一备考学生
- 当前作文约 9 分、目标 20 分的考生（默认配置，可调整）
- **不适用于**：四六级、雅思、托福作文备考

## 安装

将 `SKILL.md` 放入你的 Claude Code 项目的 `.claude/skills/` 目录下，同时保留 `data/`、`references/`、`scripts/` 文件夹结构。

```
.claude/skills/kaoyan-writing-coach/
├── SKILL.md
├── data/
│   └── profile.json
├── references/
│   ├── error-codes.md
│   ├── vocab-5500.md
│   └── vocab-5500-full.md
└── scripts/
    └── vocab_check.py
```

## 触发关键词

在 Claude Code 中提及以下关键词即可触发本 Skill：
- 考研作文 / 考研翻译 / 考研写作 / 大作文 / 翻译练习
- 批改作文 / 批改翻译 / 评分
- 出题 / 出一套题 / 写作训练 / 翻译训练

## 作者

GitHub: [@Xuehenggo](https://github.com/Xuehenggo)
