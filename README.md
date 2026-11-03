# 老张的情报站 🏴

自动采集全球 **时政 / 财金 / 军事 / 科技** 主流媒体 RSS，用 AI 翻译成中文，每 5 小时自动更新一次，生成静态网页部署在 Netlify。

## 定时更新时间（北京时间）

| 时间 | 说明 |
|---|---|
| 06:00 | 早报 |
| 09:00 | 早盘前 |
| 14:00 | 午盘 |
| 21:00 | 晚间综述 |

## 一次性部署步骤

### 1. 建 GitHub 仓库并上传代码

1. 在 GitHub 新建一个仓库（比如叫 `laozhang-intel`），**公开或私有都行**
2. 把本文件夹所有文件 push 上去：

```bash
cd laozhang-intel
git init
git add .
git commit -m "init"
git branch -M main
git remote add origin https://github.com/你的用户名/laozhang-intel.git
git push -u origin main
```

### 2. 配置 LLM API Key（必须）

在 GitHub 仓库 → **Settings → Secrets and variables → Actions → New repository secret**，添加：

| Secret 名字 | 填什么 |
|---|---|
| `OPENAI_API_KEY` | 你的 LLM API Key（必填） |
| `OPENAI_BASE_URL` | API 地址，默认 `https://api.deepseek.com`（可选） |
| `OPENAI_MODEL` | 模型名，默认 `deepseek-chat`（可选） |

> 推荐用 **DeepSeek**（便宜、中文好，https://platform.deepseek.com 注册充 10 块钱能用很久）。
> 也兼容任何 OpenAI 接口：改 `OPENAI_BASE_URL` 和 `OPENAI_MODEL` 即可（如通义千问、智谱、OpenAI、Gemini 兼容口）。

### 3. 跑一次确认

到 GitHub 仓库 → **Actions** 标签 → 选「更新情报站」→ 点 **Run workflow**。
跑成功后仓库根目录会多出一个 `index.html`。

### 4. 部署到 Netlify

1. 登录 https://app.netlify.com → **Add new site → Import an existing project → GitHub**
2. 授权后选你刚建的 `laozhang-intel` 仓库
3. 构建设置全部留空（Publish 目录填 `.`，或直接用仓库里的 `netlify.toml`），点 **Deploy**
4. 等 1 分钟，拿到 `https://xxx.netlify.app` 域名

以后每次 GitHub Actions 生成新的 `index.html` 并 push，Netlify 会自动重新部署，无需任何手动操作。

## 自定义

- **加减新闻源**：编辑 `fetch_news.py` 里的 `SOURCES` 字典，按分类加 RSS 地址即可
- **每类条数**：改 `MAX_PER_SOURCE` / `MAX_PER_CATEGORY`
- **更新时间**：编辑 `.github/workflows/update.yml` 里的 cron（UTC 时间，北京时间 -8 小时）
- **标题改名**：编辑 `fetch_news.py` 里 `render_html` 的 `<h1>老张的情报站</h1>`

## 文件说明

```
laozhang-intel/
├── fetch_news.py              # 主脚本：抓 RSS + 翻译 + 生成 HTML
├── requirements.txt           # Python 依赖
├── .github/workflows/update.yml  # 定时任务
├── netlify.toml               # Netlify 配置
└── index.html                 # 自动生成，不用手改
```
