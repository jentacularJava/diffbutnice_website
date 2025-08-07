# Alt Text Generator

This helper is designed to generate alt text using AI for comic images from a specified directory. It writes metadata to a YAML file and organizes the comics metadata into a structured format.


## Setup Instructions

**Install Dependencies**

   This project uses `uv` for dependency management. To install the required dependencies, run:

   ```sh
   uv sync
   ```

**Directory Structure**

   Ensure your directory structure looks like this:
├── comics.yaml 
├── comic_files/ 
    │ 
    ├── Dentist.JPG │ 
    ├── Falsies.jpg │ 
    └── ... 
└── build_site.py


**Running the Script**

To generate the comic alt text, simply run:

```sh
uv run python build_site.py