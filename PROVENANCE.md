# Git Migration Provenance Record

This document records the original Git commit history, authors, commit timestamps, and remote repository URLs for the scraper projects that had their own Git repositories prior to consolidation into the central `hackathon-scraper` repository.

---

## 1. Devfolio Scraper

- **Original Remote URL**: `https://github.com/naveedsheriffj/devfolio.git`
- **Original Branch**: `main`
- **Commit History**:
  - **Commit Hash**: `7e16adbf1aa77ea6249060f41d98ca10d579cff3`
  - **Author**: `naveedsheriffj <naveedsheriff.j.2024.cse@rajalakshmi.edu.in>`
  - **Date**: `Mon Sep 14 19:02:48 2026 +0530`
  - **Subject**: `initial commit`
  - **Files**: `.env`, `.env.example`, `README.md`, `__pycache__/`, `hackathons.csv`, `hackathons.json`, `pyproject.toml`, `scraper.py`, `supabase_migration.sql`, `test_extraction.py`

---

## 2. Devpost Scraper

- **Original Remote URL**: `https://github.com/naveedsheriffj/devpost.git`
- **Original Branch**: `main`
- **Commit History**:
  - **Commit 1**:
    - **Commit Hash**: `2dd0f62867253429e0a52d85f9d1261f90a45fad`
    - **Author**: `naveedsheriffj <naveedsheriff.j.2024.cse@rajalakshmi.edu.in>`
    - **Date**: `Mon Sep 14 19:15:05 2026 +0530`
    - **Subject**: `commit 1`
    - **Files**: `.env`, `__pycache__/`, `hackathons.csv`, `hackathons.json`, `requirements.txt`, `scraper.py`, `supabase_migration.sql`
  - **Commit 2**:
    - **Commit Hash**: `9431089b20f9d85b560fb5ba7bc7461678e966e4`
    - **Author**: `naveedsheriffj <naveedsheriff.j.2024.cse@rajalakshmi.edu.in>`
    - **Date**: `Mon Sep 14 19:16:49 2026 +0530`
    - **Subject**: `docs: add comprehensive README and gitignore`
    - **Files**: `.gitignore`, `README.md`

---

## 3. HackerEarth Scraper

- **Original Remote URL**: `https://github.com/naveedsheriffj/hackerEarth.git`
- **Original Branch**: `main`
- **Commit History**:
  - **Commit Hash**: `7e850a465be7b917f3db56d7cf553e9089053b2f`
  - **Author**: `naveedsheriffj <naveedsheriff.j.2024.cse@rajalakshmi.edu.in>`
  - **Date**: `Mon Sep 14 19:28:40 2026 +0530`
  - **Subject**: `feat: enhance HackerEarth scraper with dynamic 17-field extraction and Supabase sync`
  - **Files**: `.env.example`, `.gitignore`, `README.md`, `hackerearth_challenges.csv`, `hackerearth_challenges.json`, `requirements.txt`, `scraper.py`, `supabase_migration.sql`, `test_dedupe.py`, `test_scraper.py`

---

## 4. Knowafest & Unstop Scrapers

Prior to consolidation, `Knowafest` and `unstop` did not contain `.git` repositories and were untracked. All their source files, tests, documentation, and SQL migrations are fully preserved.

---

## Provenance Summary

All source code, tests, documentation, sample datasets, and SQL migrations from all 5 platforms are preserved in the central repository structure. As required by security best practices and project specifications, secrets (`.env`) from early individual commits are guarded from the central repository history via `.gitignore`.
