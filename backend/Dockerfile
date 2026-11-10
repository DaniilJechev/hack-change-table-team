FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod ./
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/backend ./cmd/backend

FROM alpine:3.23
COPY --from=build /out/backend /usr/local/bin/backend
USER 10001:10001
ENV HTTP_ADDR=:8080
EXPOSE 8080
CMD ["/usr/local/bin/backend"]
