.PHONY: up down logs shell build restart

up:
	docker compose up -d --build

down:
	docker compose down

build:
	docker compose build

restart:
	docker compose restart

logs:
	docker logs -f packmount

shell:
	docker exec -it packmount bash
