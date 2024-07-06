import sqlite3

# 连接到数据库
conn = sqlite3.connect('cards.cdb')

# 创建一个游标对象
cursor = conn.cursor()

# 执行一个 SQL 查询来获取所有的表
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
print(cursor.fetchall())

# 如果你知道某个表的名称，你可以查询该表的所有数据
cursor.execute("SELECT * FROM datas;")
print(cursor.fetchall())

# 关闭连接
conn.close()