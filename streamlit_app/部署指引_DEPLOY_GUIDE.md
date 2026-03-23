# 部署指引 — Script x Video Match Analyzer

完成以下步骤后，同事只需要打开一个网址就能使用，完全不需要任何技术知识。

---

## 需要的账号（都是免费的）

1. **GitHub 账号** → https://github.com/signup
2. **Streamlit Community Cloud 账号** → https://streamlit.io/cloud（用 GitHub 账号登录即可）

---

## 第一步：把文件上传到 GitHub

1. 登录 GitHub，点右上角「+」→「New repository」
2. Repository name 填：`script-video-match`
3. 选「Public」（Streamlit 免费版需要 Public）
4. 点「Create repository」
5. 进入仓库页面，点「uploading an existing file」
6. 把以下文件全部拖进去（包含资料夹）：
   - `app.py`
   - `requirements.txt`
   - `packages.txt`
   - `.streamlit/config.toml`（这个文件让系统支持最大 500MB 上传）
7. 点「Commit changes」

---

## 第二步：部署到 Streamlit Cloud

1. 打开 https://share.streamlit.io
2. 用 GitHub 账号登录
3. 点「New app」
4. Repository 选你刚才建的 `script-video-match`
5. Branch: `main`
6. Main file path: `app.py`
7. 点「Deploy!」

等待约 3-5 分钟，部署完成后你会得到一个网址，例如：
```
https://yourname-script-video-match-app-xxxxx.streamlit.app
```

---

## 第三步：发给同事

把那个网址发给同事，他们直接打开就能用了。

**同事的完整操作流程（无需任何技术知识）：**
1. 打开链接
2. 上传视频文件（可一次上传多个）
3. 上传脚本文件（PDF 或 DOCX，可多个）
4. 检查配对是否正确（系统会自动配对）
5. 填品牌名
6. 点「开始分析」
7. 等待结果（每支影片约 2-5 分钟）
8. 下载报告

---

## 注意事项

- Streamlit 免费版每月有使用限制，50 支影片绰绰有余
- 视频文件支持最大 **500MB**（建议控制在 300MB 以内，上传更快）
- 分析速度选「标准」适合大多数 SEA 语言（Hindi、Malay、Thai、Filipino 等）
- 首次部署后第一次运行会较慢（约 5-10 分钟），因为需要下载 Whisper 模型

---

## 遇到问题？

常见问题：
- **「Error: ffmpeg not found」** → 确认 packages.txt 有上传
- **「ModuleNotFoundError」** → 确认 requirements.txt 有上传
- **上传视频很慢** → 正常，视频文件比较大

如需更新工具，修改 app.py 后重新上传到 GitHub，Streamlit 会自动更新。
