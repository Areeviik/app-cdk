import yaml
from pathlib import Path

def load_yaml(file_path: str) -> dict:
	"""
	Load a YAML file and return its content as a dictionary.

	:param file_path: Path to the YAML file.
	:return: Dictionary containing the YAML content.
	"""
	config_file = Path(file_path)
	if not config_file.exists():
		raise FileNotFoundError(f"YAML file not found at: {config_file}")
	with open(config_file, 'r') as file:
		try:
			return yaml.safe_load(file)
		except yaml.YAMLError as e:
			raise ValueError(f"Error parsing YAML file: {e}") from e
