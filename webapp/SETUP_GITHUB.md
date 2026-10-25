# 免费方案：GitHub Actions 定时生成 + GitHub Pages 托管

**总花费 0 元。** 需要你做的只有「注册账号 + 建一个仓库 + 传文件 + 开 Pages」，约 10 分钟，之后永久自动。

---

## 一、这套东西怎么工作

```
GitHub Actions（免费定时器，周一到周五 UTC 08:30 = 北京 16:30）
        ↓ 在云端装好 python + pandas，跑 etf_rotation.py
        ↓ 生成 docs/index.html 并自动提交回仓库
GitHub Pages（免费静态托管）
        ↓
手机浏览器打开 https://<用户名>.github.io/<仓库名>/  ← 每天自动更新
```

不需要你的电脑开机，不需要服务器，不需要信用卡。

## 二、动手步骤

### 1. 注册 / 登录 GitHub
打开 https://github.com → Sign up（邮箱 + 密码，免费）。

### 2. 新建仓库
右上角 **+** → **New repository**
- Repository name：`etf-daily`（随意，记住它）
- 选 **Public**（免费方案要求；代价：策略代码与回测参数公开）
- 勾 **Add a README file** → **Create repository**

> 想不公开代码？改用 GitLab（见文末），它的 Pages 在私有仓库上免费。

### 3. 上传文件
在仓库页点 **Add file → Upload files**，把 `webapp/` 里**这些文件**拖进去：

```
app.py
etf_rotation.py
roc_ladder.py
online_data.py
recent_rotations.py
etf_backtest_long.py
etf_pool.csv
tdx_adj_long_2019_2026.csv.gz
```
点 **Commit changes**。

### 4. 上传定时任务文件
**这一步必须在网页上手动建，因为 `.github` 是隐藏目录、拖拽容易漏。**

仓库页 → **Add file → Create new file**，文件名填：

```
.github/workflows/daily.yml
```

（输入 `/` 会自动建目录）然后把 `webapp/.github/workflows/daily.yml` 的内容整段粘进去 → **Commit changes**。

### 5. 开启 Pages
仓库 **Settings** → 左侧 **Pages** →
- Source: **Deploy from a branch**
- Branch: **main** ，目录选 **/docs**
- **Save**

等 1~2 分钟，页面上会出现你的网址：

```
https://<你的用户名>.github.io/etf-daily/
```

### 6. 立刻跑一次（不用等到明天）
仓库 → **Actions** 标签 → 左侧 **ETF 轮动日报 · 每日生成** → 右侧 **Run workflow** → 绿色按钮。
约 2~3 分钟跑完，刷新 Pages 网址就能看到日报。

### 7. 手机上装成「App」
- **iOS Safari**：打开网址 → 分享 → **添加到主屏幕**
- **Android Chrome**：打开网址 → 菜单 → **添加到主屏幕**

之后桌面点图标全屏打开，无地址栏，观感接近小程序。

---

## 三、注意事项（都是免费的副作用，不是故障）

| 现象 | 原因 | 处理 |
|---|---|---|
| 每天更新时间不固定，可能晚 5~20 分钟 | GitHub 免费定时器有排队延迟 | 正常，不影响 |
| 某天没更新 | 那天新浪接口取数失败，任务报错终止；**Pages 会继续显示上一份可用报告** | 去 Actions 里看红色 ✗ 那次的日志 |
| 仓库 60 天没有任何提交，定时任务会被自动停用 | GitHub 对不活跃仓库的策略 | 我们的任务每天提交一次，不会有这个问题 |
| 打开就是昨天/上一交易日的 | 报告顶部写着「锚定日」，非交易日不会产生新数据 | 正常 |

## 四、如果不想公开代码：用 GitLab（同样免费）

- GitLab 免费版：**Pages 支持私有项目**，CI 每月 400 分钟免费（本任务约 3 分钟/天，一个月 70 分钟左右）
- 把 `.github/workflows/daily.yml` 改写成 `.gitlab-ci.yml`，`pages:` 作业里把 `docs/` 声明为 artifact
- 其余步骤同理

需要我把 GitLab 版的配置文件也写好，说一声。
