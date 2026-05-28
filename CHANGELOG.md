# CHANGELOG

## [2026-05-29] Issue #14 — 前端移动端响应式布局适配

### 变更内容
- `frontend/src/layouts/BasicLayout.vue`:
  - 补全缺失 import：`watch`（vue）、`useRoute`（vue-router）、`useWindowSize`（@vueuse/core）
  - 已有移动端基础：isMobile 判断、sidebar overlay、auto-collapse watch
- `frontend/src/styles/index.scss`:
  - 新增全局 `@media (max-width: 767px)` 移动端样式块：
    - 所有 `el-col-*` 强制100%宽度（竖向堆叠）
    - `el-table` 横向滚动
    - 页面标题缩小、卡片内边距收窄
    - el-form 单列布局
    - el-descriptions 横向滚动
    - 面包屑隐藏（手机空间不足）

### 涉及文件
- `frontend/src/layouts/BasicLayout.vue`
- `frontend/src/styles/index.scss`

### 回滚方法
```bash
git revert HEAD --no-edit
```
