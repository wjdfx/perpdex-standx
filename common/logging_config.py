import os
import logging
from pathlib import Path
from .config import LOG_LEVEL, LOG_FORMAT
from logging.handlers import TimedRotatingFileHandler

def setup_logging(log_name: str = "quant"):
    """
    配置日志系统：
    - 日志目录：通过环境变量 LOG_DIR 指定，默认 ./logs
    - 正常日志：quant.log (INFO 及以上)
    - 错误日志：error.log (ERROR 及以上)
    - 控制台输出：保持原有级别和格式
    """
    LOG_DIR = os.getenv('LOG_DIR', './logs')
    log_dir = Path(LOG_DIR).resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    # 清除现有处理器（避免重复添加）
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 设置 root logger level
    root_level = logging._nameToLevel.get(LOG_LEVEL, logging.INFO)
    logging.root.setLevel(root_level)

    # 创建格式化器
    formatter = logging.Formatter(LOG_FORMAT)

    # 正常日志文件处理器 (INFO 及以上)
    normal_handler = TimedRotatingFileHandler(log_dir / f'{log_name}.log', when='midnight', interval=1, backupCount=30, encoding='utf-8')
    normal_handler.setLevel(logging.INFO)
    normal_handler.setFormatter(formatter)
    logging.root.addHandler(normal_handler)

    # 错误日志文件处理器 (ERROR 及以上)
    error_handler = TimedRotatingFileHandler(log_dir / 'error.log', when='midnight', interval=1, backupCount=30, encoding='utf-8')
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)
    logging.root.addHandler(error_handler)

    # 控制台处理器 (保持原有行为)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(root_level)
    console_handler.setFormatter(formatter)
    logging.root.addHandler(console_handler)

    # 屏蔽SDK日志输出
    # 设置GRVT SDK logger级别为WARNING，只显示警告和错误
    grvt_logger = logging.getLogger('pysdk')
    grvt_logger.setLevel(logging.WARNING)
    
    # 由于grvt_ccxt_utils.py直接使用logging.info()，我们需要添加一个过滤器来屏蔽这些日志
    # 但不影响其他模块的日志输出
    class GrvtFilter(logging.Filter):
        def filter(self, record):
            # 屏蔽来自grvt_ccxt_utils.py的INFO级别日志
            if record.levelno == logging.INFO and 'grvt_ccxt_utils.py' in record.pathname:
                return False
            return True
    
    # 将过滤器添加到所有处理器
    for handler in logging.root.handlers:
        handler.addFilter(GrvtFilter())