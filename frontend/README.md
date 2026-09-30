# ACEYE 前端控制台

这是一个不依赖构建工具和外部 CDN 的静态页面。把 `index.html`、`styles.css`、`app.js` 作为后端静态目录挂载，例如访问 `/` 时返回 `index.html`。

页面会先请求不需要身份验证的 `GET /api/health`，随后每秒请求一次 `GET /api/state`。URL 使用 `#token=...` 时，token 会保存到当前浏览器会话，并通过 `X-ACEYE-Token` 发送给其余 API。页面离开时不会自动关闭后端任务。

后端需要提供以下动作接口：

- `POST /api/start`、`/api/stop`、`/api/emergency`
- `POST /api/config`、`/api/command`
- `POST /api/prepare`（`{ "profile": "vision|ocr|sam|reasoner" }`）
- `POST /api/reason`、`/api/segment`

模型安装或准备不显示虚假的进度条；前端会提示用户查看后端日志，并以 `/api/state.modules` 的状态和消息为准。识别对象、OCR、模型文本和输入动作均按纯文本写入 DOM，页面不打开实时截图预览，也不把模型返回内容当作 HTML 执行。

## 本地静态检查

```text
node --check app.js
```

UI 的实现故意保持为普通 HTML/CSS/JavaScript，后续可以直接替换单个模块或接入打包器，而不改变后端 API 契约。
