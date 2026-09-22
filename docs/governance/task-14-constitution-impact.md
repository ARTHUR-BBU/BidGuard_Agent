# Task 14 Constitution Impact：前端工作台边界

## 结论

Task 14 有 Constitution impact，但属于呈现层和接口边界变化，不扩大 Agent 的权限，也不改变服务端正式状态的计算权。

## 允许的变化

- 浏览器可以读取服务端返回的项目、审核和证据状态；
- 浏览器可以把用户操作交给后续受控 API；
- 前端统一复用后端的五种 `DisplayStatus` wire value；
- API 错误以状态码、错误码和用户安全文案的形式呈现。

## 不允许的变化

- 前端不得自行把“待确认”改成“已满足”；
- 前端不得通过 URL 或表单扩大项目、版本、企业资料的权限范围；
- 前端不得保存 API key、供应商密钥或未经授权的业务全文；
- Task 14 不宣称上传、审核、人工决定或报告导出已经完成。

## 验证门禁

1. `DisplayStatus` 必须和后端 wire value 完全一致；
2. 状态组件必须同时有可读文字和可见样式；
3. `ApiError` 必须保留 HTTP status、code 和 user-safe message；
4. Vitest、lint、TypeScript/Vite production build 全部通过；
5. 后续 Task 15–17 只能在这些路由和类型边界上扩展，不得在页面中重新发明业务判断。
