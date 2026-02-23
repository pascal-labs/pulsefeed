from setuptools import setup, find_packages

setup(
    name="pulsefeed",
    version="1.0.0",
    description="Real-time multi-exchange crypto market data aggregation pipeline",
    author="Pascal Legate",
    url="https://github.com/pascal-labs/pulsefeed",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "websockets>=12.0",
        "websocket-client>=1.6",
        "requests>=2.31",
        "python-dotenv>=1.0",
    ],
    extras_require={
        "capture": ["pandas>=2.0"],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
)
