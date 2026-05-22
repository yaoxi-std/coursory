# Coursory

[English](README.md) / 简体中文

Coursory 是一个配合本地 agent 使用的选课规划工作区，用来抓取课程数据、筛选课程并辅助规划课表。

这个项目推荐配合本地 coding agent 使用。爬虫脚本、Parquet 数据文件等主要是给 agent 调用的实现细节；作为用户，你通常只需要打开 agent，进入这个仓库，然后用自然语言开始对话。

## 从这里开始

1. Clone 这个仓库。
2. 确保本机已经安装 `uv` 和 Python 3.14 等工具链。
3. 打开 Codex，或者其他能够自动读取 `AGENTS.md` 的本地 coding agent。例如 Codex Desktop、Codex CLI，或任何可以指向该仓库并加载项目规则的类似 agent。
4. 在 agent 会话中 `cd` 到仓库根目录。
5. 直接用自然语言开始，例如：

```text
我是清华大学 <院系> <年级> 的本科生，请使用这个仓库协助我进行选课计划。
```

agent 会先确认你是否要使用清华大学课程数据，确认当前要规划的学期，并检查本地是否已经有课程开放数据。如果数据不存在或已经过期，agent 会根据项目规则打开清华登录流程，请你手动完成登录，然后自动抓取只读的开课信息。

首次完整抓取可能需要约 10 分钟。结构化课程数据会写入 `data/processed/` 下的 Parquet 文件；用于调试和审计的原始页面缓存会写入被 git 忽略的 `data/raw/`。

## Agent 会如何协助你

数据准备好之后，你可以继续告诉 agent 自己的约束和偏好。推荐顺序是：

1. 必须选择的课程：专业课、思政课、体育课、英语课、实验课、培养方案要求等。
2. 硬性约束：不能上课的时间段、校区或地点偏好、学分上限、工作量、考试冲突、偏好的老师等。
3. 兴趣偏好：想探索的方向、通识课类别、研讨课或项目课偏好、是否希望少考试、备选课程等。

agent 可以编写本地 Python 脚本，用 Polars 读取 Parquet 课程数据，进行初筛、比较课堂、检查时间冲突。随后它会使用自己的语言模型能力做更细致的筛选和规划：解释取舍、缩小候选范围、生成不同课表方案，并记录你已经确认的选择。

已经确认的偏好和选课规划应保存在被 git 忽略的 `.local/course-planning/` 目录下，这样之后的会话可以接着之前的状态继续，而不会把个人规划数据提交到仓库。

## 重要边界

**Coursory 只用于课程数据抓取、分析和选课规划。**

**它绝不能提交选课、退课、加入候补、确认选课，或在清华系统中执行任何会改变状态的操作。** 所有正式选课操作都必须由你本人在学校官方系统中完成。

## 手动命令

大多数用户可以让 agent 在需要时运行这些命令。清华课程爬虫的主要命令是：

```bash
uv sync
uv run python crawlers/thu-courses/auth.py login
uv run python crawlers/thu-courses/auth.py status
uv run python crawlers/thu-courses/crawl_opening_info.py --semester 2026-fall
```

只读登录和 Parquet 抓取流程的技术细节见 `crawlers/thu-courses/README.md`。

## 运行时规则

面向用户的选课规划应使用 `course-planning/` 下的运行时规则：

```bash
cd course-planning
```

见 `course-planning/README.md` 和 `course-planning/AGENTS.md`。仓库根目录的 `AGENTS.md` 是给开发者和修改代码的 agent 使用的，不是普通选课规划会话的工作流说明。

## 致谢

感谢 Codex。这个项目由作者和 Codex 在半个下午的工作会话中共同完成。
