# Rock PVP Agent

人机协作从零重建的自对战 LLM agent（参考 `~/workspace/SelfPlayAgent`）。

> 构建中：目前为初始化骨架阶段（M0）。

## 重建路线图

见 [mydocs/rebuild-plan.md](mydocs/rebuild-plan.md)（项目宪法：里程碑 M0–M4 + 人机协作方法论）。

## 快速开始

```bash
uv sync
uv sync --extra ui --extra dev
cp .env.example .env   # 填写 LLM_API_KEY / LLM_BASE_URL
uv run python -m rock_pvp_agent --version
```
