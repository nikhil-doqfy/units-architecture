# Units Project – Local Setup Instructions

Please follow the below steps to set up the Units project on your local machine.

## 1. Prerequisites

Install the following:

- Git
- Docker Desktop

After installation, verify:

```bash
git --version
docker --version
docker compose version
```

Make sure Docker Desktop is running.

## 2. Clone Architecture Repository

Clone the architecture repository first:

```bash
git clone <UNITS-ARCHITECTURE-REPO-URL>
cd units-architecture
```

## 3. Create Microservices Folder

Inside the `units-architecture` repository, create the `microservices` folder:

```bash
mkdir microservices
cd microservices
```

## 4. Create Folders for Other Repositories

Create folders for the three repositories:

```bash
mkdir units-frontend
mkdir units-backend
mkdir units-finance
```

The structure should be:

```
units-architecture/
└── microservices/
    ├── units-frontend/
    ├── units-backend/
    └── units-finance/
```

## 5. Setup units-frontend

```bash
cd units-frontend
git init
git remote add origin <UNITS-FRONTEND-REPO-URL>
git fetch origin
git checkout -t origin/main
```

## 6. Setup units-backend

```bash
cd ../units-backend
git init
git remote add origin <UNITS-BACKEND-REPO-URL>
git fetch origin
git checkout -t origin/main
```

## 7. Setup units-finance

```bash
cd ../units-finance
git init
git remote add origin <UNITS-FINANCE-REPO-URL>
git fetch origin
git checkout -t origin/main
```

## 8. Verify the Structure

The final folder structure should look like:

```
units-architecture/
│
├── microservices/
│   ├── units-frontend/
│   ├── units-backend/
│   └── units-finance/
│
└── docker-compose.yml
```

## 9. Start the Application

Go back to the architecture root:

```bash
cd ../../
```

Start all services:

```bash
docker compose up -d
```

## 10. Check Running Services

Check the containers:

```bash
docker compose ps
```

To see logs:

```bash
docker compose logs -f
```

## 11. Stop the Application

To stop the application:

```bash
docker compose down
```

## Important

- Make sure you have access to all 4 private repositories.
- Make sure Docker Desktop is running.
- If the default branch is not `main`, replace `main` with the correct branch name.
- Do not commit or push changes to any repository unless instructed.
