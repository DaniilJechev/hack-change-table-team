FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -o /backend ./cmd/backend

FROM alpine:3.22
RUN apk add --no-cache ca-certificates
RUN addgroup -S buddy && adduser -S -G buddy buddy
COPY --from=build /backend /usr/local/bin/backend
USER buddy
EXPOSE 8080
ENTRYPOINT ["backend"]
