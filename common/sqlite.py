import sqlite3
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Tuple

@contextmanager
def get_connection(db_path: str):
    """获取SQLite连接的上下文管理器"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 支持字典式访问结果
    try:
        yield conn
    finally:
        conn.close()

def init_db(db_path: str, tables: Optional[Dict[str, str]] = None) -> None:
    """
    初始化数据库，创建表
    
    :param db_path: 数据库路径
    :param tables: 字典 {表名: 建表SQL片段}, 如 {"users": "id INTEGER PRIMARY KEY, name TEXT"}
    """
    if tables is None:
        tables = {}
    
    with get_connection(db_path) as conn:
        for table_name, schema in tables.items():
            sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({schema})"
            conn.execute(sql)
        conn.commit()

def insert(db_path: str, table: str, data: Dict[str, Any]) -> int:
    """
    插入单条记录
    
    :param db_path: 数据库路径
    :param table: 表名
    :param data: 字典形式的字段数据
    :return: 新插入记录的ID
    """
    keys = ', '.join(data.keys())
    placeholders = ', '.join('?' * len(data))
    sql = f"INSERT INTO {table} ({keys}) VALUES ({placeholders})"
    
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, list(data.values()))
        conn.commit()
        return cursor.lastrowid

def insert_many(db_path: str, table: str, data_list: List[Dict[str, Any]]) -> List[int]:
    """
    批量插入记录
    
    :param db_path: 数据库路径
    :param table: 表名
    :param data_list: 记录列表，每个是字典
    :return: 新插入记录的ID列表
    """
    if not data_list:
        return []
    
    keys = list(data_list[0].keys())
    placeholders = ', '.join('?' * len(keys))
    sql = f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({placeholders})"
    
    with get_connection(db_path) as conn:
        cursor = conn.executemany(sql, [list(d.values()) for d in data_list])
        conn.commit()
        return [row[0] for row in cursor.lastrowid] if hasattr(cursor, 'lastrowid') else []

def update(db_path: str, table: str, data: Dict[str, Any], condition: str, condition_params: Tuple = ()) -> int:
    """
    更新记录
    
    :param db_path: 数据库路径
    :param table: 表名
    :param data: 要更新的字段字典
    :param condition: WHERE条件，如 "id = ?"
    :param condition_params: WHERE参数元组
    :return: 受影响行数
    """
    set_clause = ', '.join([f"{k} = ?" for k in data.keys()])
    sql = f"UPDATE {table} SET {set_clause} WHERE {condition}"
    params = list(data.values()) + list(condition_params)
    
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.rowcount

def delete(db_path: str, table: str, condition: str, params: Tuple = ()) -> int:
    """
    删除记录
    
    :param db_path: 数据库路径
    :param table: 表名
    :param condition: WHERE条件，如 "id = ?"
    :param params: WHERE参数元组
    :return: 受影响行数
    """
    sql = f"DELETE FROM {table} WHERE {condition}"
    
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        conn.commit()
        return cursor.rowcount

def select(db_path: str, table: str, columns: str = "*", condition: str = "", params: Tuple = (),
           order_by: str = "", limit: int = 0) -> List[Dict[str, Any]]:
    """
    查询记录
    
    :param db_path: 数据库路径
    :param table: 表名
    :param columns: 查询列，默认为 "*"
    :param condition: WHERE条件
    :param params: WHERE参数
    :param order_by: ORDER BY 子句
    :param limit: LIMIT 数量
    :return: 记录列表，每个是字典
    """
    sql = f"SELECT {columns} FROM {table}"
    if condition:
        sql += f" WHERE {condition}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    if limit:
        sql += f" LIMIT {limit}"
    
    with get_connection(db_path) as conn:
        cursor = conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

def select_one(db_path: str, table: str, columns: str = "*", condition: str = "", params: Tuple = ()) -> Optional[Dict[str, Any]]:
    """
    查询单条记录
    
    :param db_path: 数据库路径
    :param table: 表名
    :param columns: 查询列
    :param condition: WHERE条件
    :param params: WHERE参数
    :return: 字典或 None
    """
    records = select(db_path, table, columns, condition, params, limit=1)
    return records[0] if records else None

# 示例用法
if __name__ == "__main__":
    DB_PATH = "data/test.db"
    
    # 初始化数据库
    tables = {
        "users": "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, age INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    }
    init_db(DB_PATH, tables)
    
    # 插入
    user_id = insert(DB_PATH, "users", {"name": "Alice", "age": 30})
    print(f"Inserted user ID: {user_id}")
    
    # 查询
    users = select(DB_PATH, "users")
    print("All users:", users)
    
    # 更新
    update(DB_PATH, "users", {"age": 31}, "id = ?", (user_id,))
    
    # 查询单条
    user = select_one(DB_PATH, "users", condition="id = ?", params=(user_id,))
    print("Updated user:", user)
    
    # 删除
    delete(DB_PATH, "users", "id = ?", (user_id,))
    print("Deleted user")