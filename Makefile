.PHONY: install run test predict evaluate docker-up

install:
	python -m pip install -r requirements.txt

run:
	python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

test:
	python -m pytest -q

predict:
	@test -n "$(IN)" -a -n "$(OUT)" || (echo 'Использование: make predict IN=tickets.jsonl[.gz] OUT=pred.jsonl'; exit 2)
	python -m app.predict --in "$(IN)" --out "$(OUT)"

evaluate:
	@test -n "$(IN)" -a -n "$(PRED)" || (echo 'Использование: make evaluate IN=tickets_valid.jsonl PRED=pred.jsonl'; exit 2)
	python -m app.evaluate --in "$(IN)" --pred "$(PRED)"

docker-up:
	docker compose up --build
