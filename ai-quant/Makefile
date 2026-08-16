.PHONY: build up down logs shell init backfill evolve psql clean

build: docker-compose build
up: docker-compose up -d
down: docker-compose down
logs: docker-compose logs -f app
shell: docker-compose exec app bash
init: docker-compose run --rm app python main.py --init
backfill: docker-compose run --rm app python main.py --backfill $(YEARS)
evolve: docker-compose exec app python main.py --evolve
psql: docker-compose exec postgres psql -U ai_quant_user -d ai_quant
clean: docker-compose down -v
