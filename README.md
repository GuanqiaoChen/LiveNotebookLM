# LiveNotebookLM

NotebookLM 增强版，核心亮点：**支持语音交互（实时对话）**，而当前 NotebookLM 仅能生成语音概览和视频概览。

## Tech Stack

| 组件 | 技术 |
|------|------|
| Agent 框架 | Google ADK (Agent Development Kit) |
| 实时语音 | Gemini Live API Toolkit in ADK |
| 模型 | 支持 Live API 的 Gemini 模型 |
| 云托管 | Google Cloud Run |
| IaC / 自动化 | Terraform + deploy.sh |
| 环境 | Python + FastAPI + Docker + Cloud Storage |

## 项目结构

```
LiveNotebookLM/
├── app/
│   ├── main.py                 # FastAPI 应用入口
│   ├── .env                    # 环境变量
│   ├── __init__.py           
│   ├── static/.gitkeep         # 前端占位
│   └── live_notebook_agent/    # ADK Agent 模块
│       ├── __init__.py
│       └── agent.py            # ADK root_agent 定义
├── terraform/
│   ├── main.tf                 # Cloud Run, GCS, Artifact Registry
│   ├── variables.tf
│   ├── outputs.tf
│   └── terraform.tfvars.example
├── Dockerfile
├── deploy.sh                   # Terraform + Cloud Build 一键部署脚本
├── pyproject.toml
├── .dockerignore
├── .gitignore
└── README.md
```

## 环境准备

### 1. Python 环境 (>=3.10)

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
source .venv/bin/activate

pip install -e .
```

### 2. 环境变量

复制 `app/.env.example` 为 `app/.env` 并填写：

```bash
cp app/.env.example app/.env
```

**主要变量：**

| 变量 | 说明 |
|------|------|
| `GOOGLE_GENAI_USE_VERTEXAI` | `true`=Vertex AI, `false`=Gemini API |
| `GOOGLE_API_KEY` | Gemini API 密钥 (aistudio.google.com/apikey) |
| `GOOGLE_CLOUD_PROJECT` | GCP 项目 ID (Vertex AI 时) |
| `GOOGLE_CLOUD_LOCATION` | GCP 区域 (Vertex AI 时) |
| `LIVE_NOTEBOOK_AGENT_MODEL` | 支持 Live API 的模型名称 |

### 3. 本地运行

```bash
cd app
$env:SSL_CERT_FILE = (python -m certifi)   # Windows
# export SSL_CERT_FILE=$(python -m certifi)  # macOS/Linux
uvicorn main:app --reload --host 0.0.0.0 --port 8080
```

访问 http://localhost:8080

## 部署到 Cloud Run

### 前置条件

- 已安装 [gcloud CLI](https://cloud.google.com/sdk/docs/install)
- 已安装 [Terraform](https://www.terraform.io/downloads)
- 已登录：`gcloud auth login` 且 `gcloud auth application-default login`

### 部署

```bash
./deploy.sh YOUR_GCP_PROJECT_ID us-central1
```

或设置环境变量：

```bash
export GOOGLE_CLOUD_PROJECT=your-project-id
./deploy.sh
```

deploy.sh 将依次执行：

1. Terraform apply（创建 Artifact Registry、GCS 桶、Cloud Run 服务）
2. Cloud Build 构建并推送镜像
3. Terraform apply 更新 Cloud Run 为新镜像

### Terraform 单独使用

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# 编辑 terraform.tfvars
terraform init
terraform plan -var="image=REGION-docker.pkg.dev/PROJECT/live-notebook-lm/IMAGE:TAG"
terraform apply
```

## 待实现功能

- [ ] WebSocket `/ws/{user_id}/{session_id}` 双向语音/文本流
- [ ] ADK `Runner` + `SessionService` + `run_live()` 集成
- [ ] 文档 Grounding 工具（NotebookLM 风格）
- [ ] 前端 UI（语音/文本交互界面）

## 参考

- [ADK Gemini Live API Toolkit](https://google.github.io/adk-docs/streaming/)
- [ADK Bidi Demo](https://github.com/google/adk-samples/tree/main/python/agents/bidi-demo)
- [Vertex AI Live API](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/live-api)
- [Gemini Live API](https://ai.google.dev/gemini-api/docs/live)
