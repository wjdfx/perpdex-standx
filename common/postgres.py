import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Tuple

from common.config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER,
    POSTGRES_PASSWORD, POSTGRES_DB
)


@contextmanager
def get_connection():
    """获取PostgreSQL连接的上下文管理器"""
    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB,
        cursor_factory=RealDictCursor  # 支持字典式访问结果
    )
    try:
        yield conn
    finally:
        conn.close()


def init_db(tables: Optional[Dict[str, str]] = None) -> None:
    """
    初始化数据库，创建表
    
    :param tables: 字典 {表名: 建表SQL片段}, 如 {"users": "id SERIAL PRIMARY KEY, name TEXT"}
    """
    if tables is None:
        tables = {}
    
    with get_connection() as conn:
        cur = conn.cursor()
        for table_name, schema in tables.items():
            sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({schema})"
            cur.execute(sql)
        conn.commit()


def insert(table: str, data: Dict[str, Any]) -> int:
    """
    插入单条记录
    
    :param table: 表名
    :param data: 字典形式的字段数据（不包含id）
    :return: 新插入记录的ID
    """
    keys = ', '.join(data.keys())
    placeholders = ', '.join('%s' * len(data))
    sql = f"INSERT INTO {table} ({keys}) VALUES ({placeholders}) RETURNING id"
    
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, list(data.values()))
        new_id = cur.fetchone()['id']
        conn.commit()
        return new_id


def insert_many(table: str, data_list: List[Dict[str, Any]]) -> List[int]:
    """
    批量插入记录
    
    :param table: 表名
    :param data_list: 记录列表，每个是字典
    :return: 新插入记录的ID列表
    """
    if not data_list:
        return []
    
    keys = ', '.join(data_list[0].keys())
    placeholders = ', '.join('%s' * len(keys))
    sql = f"INSERT INTO {table} ({keys}) VALUES ({placeholders}) RETURNING id"
    
    with get_connection() as conn:
        cur = conn.cursor()
        ids = []
        for data in data_list:
            cur.execute(sql, list(data.values()))
            ids.append(cur.fetchone()['id'])
        conn.commit()
        return ids


def update(table: str, data: Dict[str, Any], condition: str, condition_params: Tuple = ()) -> int:
    """
    更新记录
    
    :param table: 表名
    :param data: 要更新的字段字典
    :param condition: WHERE条件，如 "id = %s"
    :param condition_params: WHERE参数元组
    :return: 受影响行数
    """
    set_clause = ', '.join([f"{k} = %s" for k in data.keys()])
    sql = f"UPDATE {table} SET {set_clause} WHERE {condition}"
    params = list(data.values()) + list(condition_params)
    
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount


def delete(table: str, condition: str, params: Tuple = ()) -> int:
    """
    删除记录
    
    :param table: 表名
    :param condition: WHERE条件，如 "id = %s"
    :param params: WHERE参数元组
    :return: 受影响行数
    """
    sql = f"DELETE FROM {table} WHERE {condition}"
    
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount


def select(table: str, columns: str = "*", condition: str = "", params: Tuple = (),
           order_by: str = "", limit: int = 0) -> List[Dict[str, Any]]:
    """
    查询记录
    
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
    
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def select_one(table: str, columns: str = "*", condition: str = "", params: Tuple = ()) -> Optional[Dict[str, Any]]:
    """
    查询单条记录
    
    :param table: 表名
    :param columns: 查询列
    :param condition: WHERE条件
    :param params: WHERE参数
    :return: 字典或 None
    """
    records = select(table, columns, condition, params, limit=1)
    return records[0] if records else None


# 示例用法
if __name__ == "__main__":
    # 初始化数据库
    tables = {
        "users": "id SERIAL PRIMARY KEY, name TEXT NOT NULL, age INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
    }
    init_db(tables)
    
    # 插入
    user_id = insert("users", {"name": "Alice", "age": 30})
    print(f"Inserted user ID: {user_id}")
    
    # 查询
    users = select("users")
    print("All users:", users)
    
    # 更新
    update("users", {"age": 31}, "id = %s", (user_id,))
    
    # 查询单条
    user = select_one("users", condition="id = %s", params=(user_id,))
    print("Updated user:", user)
    
    # 删除
    delete("users", "id = %s", (user_id,))
    print("Deleted user")