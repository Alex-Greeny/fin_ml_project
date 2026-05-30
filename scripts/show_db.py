import pandas as pd
import sqlite3

conn = sqlite3.connect('trades.db')

df = pd.read_sql_query("SELECT * FROM trades_history", conn)

pd.set_option('display.max_columns', None)
pd.set_option('display.width', 1000)

print(df)
    
conn.close()