import asyncio
from pytdx.hq import TdxHq_API

async def probe():
    api = TdxHq_API()
    with api.connect('119.147.212.81', 7709):
        # get_and_parse_block_info can return various blocks based on filename
        filenames = ["block_zs.dat", "block_fg.dat", "block_gn.dat", "block.dat"]
        for fn in filenames:
            try:
                data = api.get_and_parse_block_info(fn)
                if data:
                    print(f"File: {fn}, length: {len(data)}")
                    names = sorted(list(set([x['blockname'] for x in data])))
                    print(f"  First 5 names: {names[:5]}")
                    targets = ["软件服务", "半导体", "银行", "证券", "白酒", "汽车整车", "医疗服务"]
                    found = [n for n in targets if n in names]
                    if found: print(f"  Found targets: {found}")
            except Exception as e:
                print(f"Error {fn}: {e}")

if __name__ == "__main__":
    asyncio.run(probe())
