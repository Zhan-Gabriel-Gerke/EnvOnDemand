FROM ubuntu:latest
LABEL authors="zange"

ENTRYPOINT ["top", "-b"]