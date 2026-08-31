# tmpdocs — 项目文档归档

本目录汇总了项目从规划、设计到验收的全部文档，按主题归档到子目录。多数设计文档写于开发过程中，
**以当时的代码与决策为准**，其中部分早期方案可能已被后续实现取代——请优先参考代码本身与各目录下的
`README.md`，本目录主要用于追溯设计意图与决策依据。

## 目录索引

| 子目录 | 内容 |
|---|---|
| [`roadmap/`](roadmap/) | 顶层规划：项目路线图、重建计划、协作协议、审计计划 |
| [`milestones/`](milestones/) | 里程碑级文档（见下） |
| [`engine/`](engine/) | E 线引擎设计与机制文档（数据契约、技能/特性/印记/天气/进化、扩展手册） |
| [`data/`](data/) | 数据文件：精灵/技能 JSON、技能批次、特性批次 |
| [`audit/`](audit/) | 引擎审计报告（确定性/迷雾/重放三不变式） |
| [`reference/`](reference/) | 参考论文（GEPA / MemRL / SkillOpt / AMC 等）及提取文本 |
| [`archive/`](archive/) | 废案 / 早期中间产物 / 对话记录 |

## milestones/ 细分

| 子目录 | 内容 |
|---|---|
| [`milestones/checkpoints/`](milestones/checkpoints/) | 每个里程碑的 Gate 验收记录（E0a–E7 / M0–M4 / R0–R5 / S0 / 记忆注入） |
| [`milestones/chatmode/`](milestones/chatmode/) | M 线组队顾问的详细设计（chatmode plan v1/v2 + M1–M6 详设） |
| [`milestones/evolution/`](milestones/evolution/) | R 线自博弈进化方案（self-rl-evolution-plan + R0–R5 复盘 + 计划文档） |

## 阅读建议

1. **先看代码**：`src/*/README.md` 是每个模块的「活的」说明，与代码同步。
2. **再看路线图**：[`roadmap/project-roadmap.md`](roadmap/project-roadmap.md) 给出三线统筹的全局视角。
3. **追溯决策**：按需查阅对应里程碑的 checkpoint（Gate 记录）与详设。
4. **早期文档慎读**：`engine/`、`milestones/` 里的方案文档记录的是「当时的设计意图」，实现可能已演进。
