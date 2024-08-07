from dotenv import find_dotenv, load_dotenv


def init_env():
    path = find_dotenv()
    if not path:
        print("Env file not found")
        return
    load_dotenv(path)
    print(f'Env file loaded from "{path}"')


init_env()
