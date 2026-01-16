# perpdex 策略脚本

## 简介

这是一个与各个perpdex进行交互的脚本应用，目前支持lighter和grvt的网格，以及standx单纯做市。

## 快速开始

### 1. 创建虚拟环境

建议使用 Python 虚拟环境来运行此项目，以避免依赖冲突。

```bash
# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

### 2. 安装依赖

在激活虚拟环境后，安装项目依赖：

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

首次运行时，复制 `.env.example` 文件为 `.env`，并根据您的需求修改配置：

```bash
cp .env.example .env
```

然后编辑 `.env` 文件，配置相关参数如交易对、挂单距离、最大持仓等。

### 4. 生成 Keystore 文件

项目支持两种方式生成 keystore 文件：

- 生成新地址
- 导入现有私钥

运行以下命令生成 keystore 文件：

```bash
python eth_keystore.py
```

按照提示选择生成新地址或导入私钥，并完成相关操作。

### 5. 运行做市脚本

最后，运行以下命令启动做市机器人：

```bash
python only_maker.py
```

## 配置说明

项目的主要配置在 `.env` 文件中，包括：

- 交易所配置（如 API 地址、私钥等）
- 做市策略参数（如挂单距离、最大持仓等）
- 日志配置（如日志级别、日志文件目录等）

详细配置说明请参考 `.env.example` 文件中的注释。

## 注意事项

- 请确保在运行做市机器人之前，已正确配置 `.env` 文件和 keystore 文件。
- 做市机器人会根据配置的策略自动挂单和撤单，请确保您的账户有足够的资金和权限。
- 建议在测试环境中先进行测试，确保一切正常后再在生产环境中运行。
- 建议做市账号不要在客户端进行其他手动操作，以避免相互影响。
- 建议开启STANDX_MAKER_FIX_ORDER_ENABLED，做到被吃单后修复仓位。
- 如需自动平仓模式（挂单成交后立即市价平仓，避免持仓风险），可开启 `STANDX_MAKER_AUTO_CLOSE_POSITION=true`。默认关闭，不影响原有做市逻辑。