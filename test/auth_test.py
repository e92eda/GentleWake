import asyncio
import getpass
from alexapy import AlexaLogin, AlexaAPI

async def main():
    email = input("Amazon.co.jp メールアドレス: ")
    password = getpass.getpass("パスワード: ")

    login = AlexaLogin(
        url="amazon.co.jp",
        email=email,
        password=password,
        outputpath=lambda filename: f"./{filename}",
        debug=False,
    )

    await login.login()

    if login.status.get("login_successful"):
        print("ログイン成功！")
        devices = await AlexaAPI.get_devices(login)
        print("デバイス一覧:")
        for d in devices:
            print(f"  - {d['accountName']} ({d['deviceFamily']})")
    else:
        print("ログイン失敗:", login.status)

asyncio.run(main())
