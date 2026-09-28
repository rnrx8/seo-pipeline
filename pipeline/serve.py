"""Container entrypoint: resolve PORT in Python, without relying on shell expansion."""
import os
import uvicorn


def main():
    uvicorn.run('api_server:app', host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))


if __name__ == '__main__':
    main()
