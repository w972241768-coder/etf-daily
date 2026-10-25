import sys
sys.path.insert(0, '.')
import online_data as od

codes = ['515880', '159516', '588200', '159530']
for c in codes:
    try:
        df = od.fetch_online([c], datalen=5)
        if df is None or not len(df):
            print(f'{c}: 取数失败')
            continue
        print(f'{c}: {len(df)} 根, 列={list(df.columns)}')
        print(df.head(3).to_string())
    except Exception as e:
        print(f'{c}: 异常 {type(e).__name__}: {e}')
    print('-' * 60)
