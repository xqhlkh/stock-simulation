# 模拟炒股系统

支持A股、港股、美股的历史回测和实时模拟盘，支持做多做空，多账户独立计算收益。

## 功能特性

- **三大市场**：A股、港股、美股全覆盖
- **双模式**：实时模拟盘 + 历史回测
- **做多做空**：支持双向交易
- **多账户**：每个账户独立计算收益
- **资产曲线**：Chart.js 可视化每日资产变化
- **汇率换算**：港股/美股自动换算为CNY显示
- **数据缓存**：历史价格缓存到SQLite，避免重复请求

## 技术栈

- 后端：Python + FastAPI
- 数据：AKShare（历史行情 + 实时行情）
- 数据库：SQLite
- 前端：单页 HTML + Vanilla JS + Chart.js

## 安装部署（Linux 服务器）

### 1. 安装 Python 3.10+

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3 python3-pip python3-venv -y

# CentOS/RHEL
sudo yum install python3 python3-pip -y
```

### 2. 克隆项目

```bash
cd /opt
git clone <你的仓库地址> stock-sim
cd stock-sim
```

### 3. 创建虚拟环境并安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. 启动服务

```bash
# 前台运行（开发测试用）
python main.py

# 后台运行（推荐用 screen 或 nohup）
nohup python main.py > stock_sim.log 2>&1 &

# 或者用 screen
screen -S stock-sim
python main.py
# Ctrl+A+D 退出 screen
```

### 5. 访问网站

浏览器打开：`http://你的服务器IP:8000`

## 使用说明

### 创建账户

1. 点击左上角「+ 新建」按钮
2. 输入账户名称，选择模式（实时/回测），设置初始资金
3. 点击「创建」

### 实时模拟盘

1. 选择账户后自动进入实时模式
2. 在左栏输入股票代码（如 `000001`），选择市场（A/HK/US），点击「查询」
3. 查看行情信息后，选择方向（做多/做空），输入数量
4. 点击「确认开仓」
5. 右栏查看持仓列表，随时可以「平仓」
6. 资产曲线每60秒自动刷新

### 历史回测

1. 切换到「历史回测」模式
2. 使用导航栏的日期控制：前一天/后一天/跳转
3. 查询指定日期的股票价格
4. 按当日价格开仓/平仓
5. 查看资产曲线回测效果

### 股票代码格式

| 市场 | 格式 | 示例 |
|------|------|------|
| A股  | 6位数字 | 000001（平安银行）、600519（贵州茅台） |
| 港股 | 5位数字 | 00700（腾讯）、09988（阿里巴巴） |
| 美股 | 英文字母 | AAPL（苹果）、TSLA（特斯拉） |

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /accounts | 获取所有账户 |
| POST | /accounts | 创建账户 |
| DELETE | /accounts/{id} | 删除账户 |
| GET | /quote | 获取行情（支持回测日期） |
| GET | /quote/range | 获取历史价格区间 |
| POST | /trade/open | 开仓 |
| POST | /trade/close | 平仓 |
| GET | /positions/{id} | 获取持仓及盈亏 |
| GET | /snapshot/{id} | 获取资产曲线数据 |
| GET | /calendar/prev | 上一交易日 |
| GET | /calendar/next | 下一交易日 |

## 交易规则

### A股
- 涨跌停限制：普通股 ±10%，科创板/创业板 ±20%
- T+1规则：当日买入不可当日卖出
- 交易时段：09:30-11:30 / 13:00-15:00（北京时间）

### 港股
- 无涨跌停限制
- 交易时段：09:30-16:00（香港时间）
- 价格单位 HKD，自动换算为 CNY

### 美股
- 无涨跌停限制
- 交易时段：09:30-16:00 ET
- 价格单位 USD，自动换算为 CNY

## 文件结构

```
stock-sim/
├── main.py          # FastAPI 主入口
├── database.py      # 数据库操作层
├── data_fetcher.py  # AKShare 数据获取层
├── trading.py       # 交易业务逻辑层
├── routes.py        # API 路由
├── index.html       # 前端页面
├── requirements.txt # 依赖
└── README.md        # 说明文档
```

## 注意事项

1. 首次运行会自动创建 SQLite 数据库文件 `stock_sim.db`
2. 历史数据会缓存在数据库中，避免重复请求 AKShare
3. AKShare 接口有频率限制，建议不要过于频繁查询
4. 实时行情依赖网络连接，确保服务器可访问互联网
5. 如需修改端口，编辑 `main.py` 中的 `port=8000`
