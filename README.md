# Setup

Built using python 3.12.6

Dependency links

* [uv](https://github.com/astral-sh/uv)

Run `uv sync` to install dependencies

Run `uv tool install -e .` in the root directory to make `composify` executable globally.


# Usage

`uv append` - Appends the given docker image to an already existing docker-compose file in the stacks directory.

`uv new` - Creates a new folder with a docker-compose file in the stacks directory.

`uv create-db` Creates a DB service in an already existing docker-compose file in the stacks directory. Generates and stores secrets in the SECRETSDIR and adds secrets to the root compose file.
