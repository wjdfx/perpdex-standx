# StandX ETH 主网网格交易程序

本项目已精简为 `StandX` 专用版本，仅支持：
- StandX 主网
- ETH 市场（`ETH-USD`）
- Python 3.12
- API Token 认证

## 1. 环境准备

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 配置

```bash
cp .env.example .env
```

必须配置：
- `STANDX_API_TOKEN`
- `STANDX_REQUEST_SIGN_PRIVATE_KEY`

Token 获取和签名说明请参考官方文档：
- [StandX Docs](https://docs.standx.com/docs/about-stand-x)
- [StandX API](https://docs.standx.com/standx-api/standx-api)

## 3. 启动

```bash
python grid.py
```

## 4. 代理（可选）

如果网络需要代理，可先执行：

```bash
export https_proxy=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890
export all_proxy=socks5://127.0.0.1:7890
```

或在 `.env` 中设置 `PROXY_URL`。
