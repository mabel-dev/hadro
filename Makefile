lint:
	python -m pip install --quiet --upgrade pycln isort black
	python -m pycln .
	python -m isort .
	python -m black .

update:
	python -m pip install --upgrade pip
	python -m pip install --upgrade -r requirements.txt
	python -m pip install --upgrade -r tests/requirements.txt

test:
	clear
	python -m pytest

coverage:
	clear
	python -m coverage run -m pytest 
	python -m coverage report --include=mabel/** -m

compile:
	python setup.py build_ext --inplace