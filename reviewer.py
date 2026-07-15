"""
Review Engine — tries Groq (cloud LLM) first, falls back to static analysis.

Priority:
  1. Groq    — free cloud LLM, needs GROQ_API_KEY in .env
  2. Static  — built-in pattern checks, always works
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from typing import Callable

from ingestion import SourceFile, collect_stats
from models import (
    Finding, FinalVerdict, ReviewReport, RoadmapItem, Scorecard,
)

logger = logging.getLogger(__name__)


# ── Individual check helpers ──────────────────────────────────

def _snippet(content: str, match_start: int, context: int = 1) -> str:
    """Return the matched line plus `context` lines either side, stripped."""
    lines = content.splitlines()
    line_no = content[:match_start].count("\n")
    lo = max(0, line_no - context)
    hi = min(len(lines) - 1, line_no + context)
    return " | ".join(lines[lo:hi + 1]).strip()[:200]


def _findings_secrets(files: list[SourceFile]) -> list[Finding]:
    """Detect hardcoded secrets / credentials."""
    PATTERNS = [
        (re.compile(r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']'),  "password"),
        (re.compile(r'(?i)(api_key|apikey|secret_key|secret)\s*=\s*["\'][^"\']{8,}["\']'), "API key or secret"),
        (re.compile(r'(?i)(token)\s*=\s*["\'][^"\']{8,}["\']'),               "access token"),
        (re.compile(r'(?i)(aws_access_key_id|aws_secret)\s*=\s*["\'][^"\']{8,}["\']'), "AWS credential"),
        (re.compile(r'(?i)-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'),   "private key"),
    ]
    results: list[Finding] = []
    fid = 1
    for sf in files:
        for pattern, label in PATTERNS:
            for m in pattern.finditer(sf.content):
                line_no = sf.content[: m.start()].count("\n") + 1
                snip = _snippet(sf.content, m.start())
                results.append(Finding(
                    id=f"SEC-{fid:03d}",
                    severity="Critical",
                    category="Security",
                    file_path=sf.path,
                    line_numbers=str(line_no),
                    description=(
                        f"A {label} is written directly into the code at "
                        f"{sf.path}, line {line_no}: `{snip}`"
                    ),
                    root_cause=(
                        f"Someone typed the actual {label} value straight into the source file "
                        f"on line {line_no}, instead of storing it in a safe place outside the code. "
                        f"Anyone who can read this file — including anyone who has ever cloned the "
                        f"repository — can see and use it."
                    ),
                    business_impact=(
                        f"If this code is ever shared, uploaded, or its history is viewed by "
                        f"someone unauthorised, they can use the exposed {label} to log in as your "
                        f"service, access private data, run up costs on cloud accounts, or cause "
                        f"a data breach that triggers legal and financial penalties."
                    ),
                    technical_impact=(
                        f"The {label} on line {line_no} of {sf.path} is stored in plain text "
                        f"and will appear in every backup, log, and copy of this code. "
                        f"Changing it requires editing the code and redeploying — you cannot "
                        f"update it quickly if it gets stolen."
                    ),
                    recommendation=(
                        f"Step 1 — Remove the {label} from line {line_no} right now and save the file.\n"
                        f"Step 2 — If this code has ever been shared or committed to git, "
                        f"treat the {label} as already stolen and invalidate/rotate it immediately.\n"
                        f"Step 3 — Store the value in an environment variable instead. "
                        f"In your code, read it with: os.environ['YOUR_VARIABLE_NAME']\n"
                        f"Step 4 — Never commit real credentials to source code again."
                    ),
                    effort="Small",
                ))
                fid += 1
    return results


def _findings_sql_injection(files: list[SourceFile]) -> list[Finding]:
    """Flag string-formatted SQL queries."""
    pattern = re.compile(
        r'(?i)(execute|query|cursor\.execute)\s*\(\s*[f"\'].*(%s|%d|\{.*?\}|"?\s*\+)',
    )
    results: list[Finding] = []
    fid = 1
    for sf in files:
        if sf.language not in ("Python", "JavaScript", "TypeScript", "PHP", "Java", "C#"):
            continue
        for m in pattern.finditer(sf.content):
            line_no = sf.content[: m.start()].count("\n") + 1
            snip = _snippet(sf.content, m.start())
            results.append(Finding(
                id=f"SQLI-{fid:03d}",
                severity="Critical",
                category="Security",
                file_path=sf.path,
                line_numbers=str(line_no),
                description=(
                    f"A database query at {sf.path}, line {line_no} is built by "
                    f"pasting variables directly into the query text: `{snip}`"
                ),
                root_cause=(
                    f"On line {line_no} of {sf.path}, a database command is built "
                    f"by gluing a variable directly into the query string. "
                    f"If that variable ever contains input typed by a user, "
                    f"the user can type extra database commands and trick the system "
                    f"into running them."
                ),
                business_impact=(
                    f"An attacker who exploits this can read every record in your database, "
                    f"change or delete data, bypass login checks, and potentially take full "
                    f"control of the server. This is one of the most commonly exploited "
                    f"vulnerabilities and has caused some of the largest data breaches in history."
                ),
                technical_impact=(
                    f"The database has no way to tell the difference between the intended "
                    f"query and injected commands — it runs whatever string it receives. "
                    f"A simple fix exists (parameterised queries) and should replace this "
                    f"pattern immediately."
                ),
                recommendation=(
                    f"Never paste variables into a database query string. "
                    f"Instead, use a placeholder and pass the value separately:\n"
                    f"  Wrong:  cursor.execute(f'SELECT * FROM users WHERE id = {{user_id}}')\n"
                    f"  Right:  cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))\n"
                    f"The database driver handles the value safely so no injection is possible."
                ),
                references="OWASP A03:2021 — Injection, CWE-89",
                effort="Medium",
            ))
            fid += 1
    return results


def _findings_todos(files: list[SourceFile]) -> list[Finding]:
    """Report TODO / FIXME / HACK comments as technical debt."""
    pattern = re.compile(r"(?i)#\s*(TODO|FIXME|HACK|XXX|BUG)[:\s](.{0,80})")
    results: list[Finding] = []
    fid = 1
    for sf in files:
        for m in pattern.finditer(sf.content):
            tag = m.group(1).upper()
            msg = m.group(2).strip()
            line_no = sf.content[: m.start()].count("\n") + 1
            sev = "High" if tag in ("FIXME", "BUG", "HACK") else "Medium"
            msg_str = f'"{msg}"' if msg else "no description given"
            tag_meaning = {
                "TODO":  "a reminder that something still needs to be done",
                "FIXME": "a known problem in the code that has not been fixed yet",
                "BUG":   "a known bug that has not been fixed yet",
                "HACK":  "a quick workaround that was meant to be replaced properly",
                "XXX":   "a warning that this code is known to be wrong or dangerous",
            }.get(tag, "an unresolved note left by a developer")
            results.append(Finding(
                id=f"DEBT-{fid:03d}",
                severity=sev,
                category="Maintainability",
                file_path=sf.path,
                line_numbers=str(line_no),
                description=(
                    f"Unresolved {tag} note in {sf.path} at line {line_no}: {msg_str}."
                ),
                root_cause=(
                    f"A developer left a {tag} comment on line {line_no} of {sf.path} — "
                    f"which means {tag_meaning}. "
                    f"The note says: {msg_str}. "
                    f"This was never acted on and the problem is still in the code."
                ),
                business_impact=(
                    f"{'This is a known bug still running in production. Users may already be affected by wrong or missing behaviour.' if tag in ('FIXME', 'BUG') else ''}"
                    f"{'This is a temporary workaround. It might break in situations the original developer did not think about.' if tag == 'HACK' else ''}"
                    f"{'This is unfinished work. Features that depend on it may not work correctly until it is completed.' if tag == 'TODO' else ''}"
                    f"{'This section of code is known to be risky or incorrect — it could cause unexpected failures.' if tag == 'XXX' else ''}"
                ).strip(),
                technical_impact=(
                    f"The code at line {line_no} is {'broken or incomplete' if tag in ('FIXME','BUG') else 'a temporary patch' if tag == 'HACK' else 'unfinished'}. "
                    f"The longer it stays, the more other parts of the code will be written around it, "
                    f"making it harder and riskier to fix later."
                ),
                recommendation=(
                    f"Step 1 — Open {sf.path} and go to line {line_no}.\n"
                    f"Step 2 — {'Fix the problem described: ' + msg + '.' if msg else 'Fix or complete the code as intended.'}\n"
                    f"Step 3 — If you cannot fix it immediately, create a task or ticket "
                    f"with that description and replace the comment with the ticket number, "
                    f"so it is tracked and not forgotten."
                ),
                effort="Small",
            ))
            fid += 1
    return results


def _findings_long_functions(files: list[SourceFile]) -> list[Finding]:
    """Flag Python functions longer than 60 lines."""
    results: list[Finding] = []
    fid = 1
    for sf in files:
        if sf.language != "Python":
            continue
        try:
            tree = ast.parse(sf.content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = node.lineno
                end = max(
                    (getattr(n, "lineno", start) for n in ast.walk(node)),
                    default=start,
                )
                length = end - start + 1
                if length > 60:
                    branches = sum(
                        1 for n in ast.walk(node)
                        if isinstance(n, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                                          ast.With, ast.Assert))
                    )
                    results.append(Finding(
                        id=f"MAINT-{fid:03d}",
                        severity="Medium",
                        category="Maintainability",
                        file_path=sf.path,
                        method_name=node.name,
                        line_numbers=f"{start}-{end}",
                        description=(
                            f"The function '{node.name}' in {sf.path} is {length} lines long "
                            f"(lines {start} to {end}). It does too many things at once."
                        ),
                        root_cause=(
                            f"'{node.name}' has grown to {length} lines with {branches} decision "
                            f"points (if/else, loops, error handlers). "
                            f"A function this long is usually trying to do several different jobs "
                            f"in one place, which makes it hard to read and easy to break."
                        ),
                        business_impact=(
                            f"When someone needs to change or fix something in '{node.name}', "
                            f"they have to read and understand all {length} lines before touching "
                            f"anything. This slows down every future change and increases the chance "
                            f"of accidentally breaking something else in the process."
                        ),
                        technical_impact=(
                            f"With {branches} decision points, '{node.name}' has "
                            f"{branches + 1} different paths through it — each needs its own test "
                            f"to be properly checked. This makes writing tests for it very time-consuming "
                            f"and most teams skip it, leaving the code untested."
                        ),
                        recommendation=(
                            f"Split '{node.name}' (lines {start}–{end}) into smaller functions, "
                            f"each with one clear job. Look for natural breaks in the code — "
                            f"places where a comment explains what the next section does. "
                            f"Each of those sections can usually become its own function. "
                            f"Aim for functions no longer than 30 lines."
                        ),
                        effort="Medium",
                    ))
                    fid += 1
    return results


def _findings_missing_tests(files: list[SourceFile]) -> list[Finding]:
    """Warn when no test files are found."""
    src_files = [f for f in files if f.language in ("Python", "JavaScript", "TypeScript", "Java", "Go")]
    test_files = [
        f for f in files
        if re.search(r"(test_|_test\.|\.spec\.|\.test\.)", f.path, re.IGNORECASE)
    ]
    if not test_files:
        langs = list({f.language for f in src_files if f.language})
        lang_hint = langs[0] if langs else "Python"
        tool = {"Python": "pytest", "JavaScript": "Jest", "TypeScript": "Jest",
                "Java": "JUnit", "Go": "go test"}.get(lang_hint, "a testing tool")
        return [Finding(
            id="TEST-001",
            severity="High",
            category="Testability",
            description=(
                f"No tests found anywhere in the project. "
                f"All {len(src_files)} source files are completely untested."
            ),
            root_cause=(
                f"The project has {len(src_files)} code files but zero test files. "
                f"Tests are separate files that automatically check whether the code "
                f"does what it is supposed to do. Without them, the only way to check "
                f"the code works is to run it manually and hope nothing goes wrong."
            ),
            business_impact=(
                f"Every time someone changes the code, there is no automatic check that "
                f"the rest of the system still works. A small change in one place can "
                f"quietly break something else, and the team will only find out when "
                f"a real user reports a problem."
            ),
            technical_impact=(
                f"Without tests, it is risky to improve or restructure any of the "
                f"{len(src_files)} existing files, because there is nothing to confirm "
                f"the changes did not break anything. This slows the team down over time "
                f"as the codebase grows."
            ),
            recommendation=(
                f"Step 1 — Install {tool} (a free testing tool for this project's language).\n"
                f"Step 2 — Pick the most important part of the code and write one test "
                f"that checks it works correctly.\n"
                f"Step 3 — Gradually add more tests as you make changes, aiming to cover "
                f"at least 80% of the important logic.\n"
                f"Step 4 — Set up your build pipeline to run tests automatically on every change."
            ),
            effort="Large",
        )]
    return []


def _findings_bare_except(files: list[SourceFile]) -> list[Finding]:
    """Flag bare `except:` clauses in Python."""
    pattern = re.compile(r"^\s*except\s*:", re.MULTILINE)
    results: list[Finding] = []
    fid = 1
    for sf in files:
        if sf.language != "Python":
            continue
        for m in pattern.finditer(sf.content):
            line_no = sf.content[: m.start()].count("\n") + 1
            try_match = sf.content[:m.start()].rfind("try:")
            try_line = sf.content[:try_match].count("\n") + 1 if try_match >= 0 else None
            try_ref = f" (which started on line {try_line})" if try_line else ""
            results.append(Finding(
                id=f"ERR-{fid:03d}",
                severity="Medium",
                category="Reliability",
                file_path=sf.path,
                line_numbers=str(line_no),
                description=(
                    f"Error handler at {sf.path}, line {line_no} catches every possible "
                    f"error without saying which ones it expects{try_ref}."
                ),
                root_cause=(
                    f"The error handler on line {line_no} of {sf.path} does not specify "
                    f"what kind of error it is handling. It will silently swallow every "
                    f"error that occurs — including serious ones like the program running "
                    f"out of memory or being asked to shut down — and the code will carry "
                    f"on as if nothing went wrong."
                ),
                business_impact=(
                    f"When something goes wrong inside this section of code, the error is "
                    f"hidden and the program continues. This means the application may give "
                    f"users wrong results, skip important steps (like saving data), or appear "
                    f"to work fine while actually failing. These problems are very hard to "
                    f"diagnose because no warning is ever recorded."
                ),
                technical_impact=(
                    f"Any error thrown in the try block{try_ref} is silently discarded. "
                    f"This includes errors that should stop the program (like out-of-memory "
                    f"or shutdown signals), so the process cannot always close cleanly."
                ),
                recommendation=(
                    f"Change the error handler on line {line_no} to name the specific "
                    f"error(s) you are expecting:\n"
                    f"  Instead of:  except:\n"
                    f"  Use:         except ValueError:\n"
                    f"  Or:          except (ValueError, KeyError) as error:\n"
                    f"                   print('Something went wrong:', error)\n"
                    f"If you really need to catch any error, write "
                    f"`except Exception as error:` and always record what went wrong."
                ),
                effort="Small",
            ))
            fid += 1
    return results


def _findings_print_debug(files: list[SourceFile]) -> list[Finding]:
    """Flag print() used as debug logging in Python."""
    pattern = re.compile(r"^\s*print\s*\(", re.MULTILINE)
    results: list[Finding] = []
    fid = 1
    for sf in files:
        if sf.language != "Python":
            continue
        if re.search(r"(test_|_test\.|setup\.py|conftest)", sf.path):
            continue
        matches = list(pattern.finditer(sf.content))
        if len(matches) >= 3:
            line_nos = [sf.content[:m.start()].count("\n") + 1 for m in matches[:4]]
            lines_str = ", ".join(str(n) for n in line_nos)
            extra = f" (and {len(matches)-4} more)" if len(matches) > 4 else ""
            results.append(Finding(
                id=f"LOG-{fid:03d}",
                severity="Low",
                category="Observability",
                file_path=sf.path,
                line_numbers=lines_str,
                description=(
                    f"{sf.path} uses print() to write messages {len(matches)} times "
                    f"(lines {lines_str}{extra}). This is fine for scripts, but not for "
                    f"production applications."
                ),
                root_cause=(
                    f"{sf.path} uses print() statements {len(matches)} times to output "
                    f"messages at runtime. In a real deployed application, print() just "
                    f"dumps text with no information about when it happened, how serious "
                    f"it is, or where it came from."
                ),
                business_impact=(
                    f"When this application is running on a server, the print() messages "
                    f"are either lost entirely or mixed in with everything else, making it "
                    f"very difficult to tell if something is going wrong. If an incident "
                    f"occurs, the team will struggle to find useful information in the logs."
                ),
                technical_impact=(
                    f"Unlike a proper logging system, print() messages cannot be filtered "
                    f"by importance level, automatically timestamped, routed to a log "
                    f"management service, or silenced without editing the code. "
                    f"They are also invisible to standard test frameworks."
                ),
                recommendation=(
                    f"Replace the {len(matches)} print() calls in {sf.path} with a logger:\n"
                    f"  Add at the top of the file:\n"
                    f"    import logging\n"
                    f"    logger = logging.getLogger(__name__)\n"
                    f"  Then replace print('something') with:\n"
                    f"    logger.info('something')    # for normal messages\n"
                    f"    logger.warning('something') # for warnings\n"
                    f"    logger.error('something')   # for errors"
                ),
                effort="Small",
            ))
            fid += 1
    return results


def _has_docstring(node: ast.AST) -> bool:
    """Return True if the node's first statement is a string constant (docstring)."""
    return bool(
        getattr(node, "body", None)
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    )


def _top_level_nodes(tree: ast.Module) -> list[ast.AST]:
    """
    Yield only top-level and class-body functions/classes — not nested ones.
    This avoids double-counting methods that belong to an already-flagged class.
    """
    nodes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            nodes.append(node)
    return nodes


def _findings_missing_docstrings(files: list[SourceFile]) -> list[Finding]:
    """Flag public Python functions/classes lacking docstrings (max 1 per file)."""
    results: list[Finding] = []
    fid = 1
    for sf in files:
        if sf.language != "Python":
            continue
        if re.search(r"(test_|_test\.)", sf.path):
            continue
        try:
            tree = ast.parse(sf.content)
        except SyntaxError:
            continue

        missing: list[tuple[str, int, str]] = []   # (name, lineno, kind)
        for node in _top_level_nodes(tree):
            if node.name.startswith("_"):
                continue
            if not _has_docstring(node):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                missing.append((node.name, node.lineno, kind))

        if not missing:
            continue

        # Emit one finding per file summarising all missing symbols — avoids
        # flooding the report with identical boilerplate findings.
        names = ", ".join(f"'{n}'" for n, _, _ in missing[:8])
        extra = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        sample_name, sample_line, sample_kind = missing[0]

        results.append(Finding(
            id=f"DOC-{fid:03d}",
            severity="Low",
            category="Documentation",
            file_path=sf.path,
            line_numbers=str(sample_line),
            description=(
                f"{len(missing)} function(s) or class(es) in {sf.path} have no "
                f"description written for them: {names}{extra}."
            ),
            root_cause=(
                f"The {sample_kind} '{sample_name}' (and {len(missing) - 1} other(s)) "
                f"in {sf.path} have no summary comment at the top explaining what they do. "
                f"These summaries — called docstrings — are how developers communicate "
                f"the purpose of code without having to read every line of it."
            ),
            business_impact=(
                f"Anyone who needs to use or change {names[:50]} has to read through "
                f"all the code inside it to figure out what it does. "
                f"This slows down new team members learning the codebase and makes "
                f"every code review and bug investigation take longer."
            ),
            technical_impact=(
                f"Code editors use these descriptions to show helpful hints as you type. "
                f"Without them, auto-complete offers no guidance, and documentation "
                f"tools cannot generate a readable reference guide for the project."
            ),
            recommendation=(
                f"Add a short description inside each of these: {names}{extra}.\n"
                f"Example — add this as the very first line inside the function:\n"
                f'  def {sample_name}(...):\n'
                f'      """Explain in one sentence what this does."""'
            ),
            effort="Small",
        ))
        fid += 1

    return results


def _findings_env_not_validated(files: list[SourceFile]) -> list[Finding]:
    """Flag os.environ.get() calls without a default or validation."""
    pattern = re.compile(r'os\.environ\.get\(\s*(["\'][A-Z_]+["\'])\s*\)')
    results: list[Finding] = []
    fid = 1
    for sf in files:
        if sf.language != "Python":
            continue
        matches = list(pattern.finditer(sf.content))
        if not matches:
            continue
        key_names = list(dict.fromkeys(m.group(1).strip("'\"") for m in matches))
        line_nos = [sf.content[:m.start()].count("\n") + 1 for m in matches[:4]]
        lines_str = ", ".join(str(n) for n in line_nos)
        extra = f" (+{len(matches)-4} more)" if len(matches) > 4 else ""
        keys_str = ", ".join(f"`{k}`" for k in key_names[:4])
        results.append(Finding(
            id=f"CFG-{fid:03d}",
            severity="Low",
            category="Configuration",
            file_path=sf.path,
            line_numbers=lines_str,
            description=(
                f"{sf.path} reads {len(matches)} setting(s) from the environment "
                f"({keys_str}) at lines {lines_str}{extra} without checking they exist first."
            ),
            root_cause=(
                f"{sf.path} reads the setting {key_names[0]!r} from the environment "
                f"{len(matches)} time(s) but never checks whether it is actually set. "
                f"If the setting is missing, the code gets nothing back (a blank value) "
                f"and continues without raising an error — even though it is broken."
            ),
            business_impact=(
                f"If {keys_str} {'is' if len(key_names) == 1 else 'are'} missing from the "
                f"server's configuration, the application will start up without any error "
                f"but then fail partway through a real user request with a confusing crash — "
                f"instead of refusing to start and telling an administrator what is missing."
            ),
            technical_impact=(
                f"A missing setting returns a blank value that gets passed silently through "
                f"the code until something tries to use it, causing a confusing error message "
                f"that points to the wrong place and makes the real cause hard to find."
            ),
            recommendation=(
                f"Check that the setting exists as soon as the application starts. "
                f"In {sf.path}, add a check like this:\n"
                f"  {key_names[0]} = os.environ.get('{key_names[0]}')\n"
                f"  if not {key_names[0]}:\n"
                f"      raise RuntimeError(\n"
                f"          'The setting {key_names[0]} is required but has not been configured. '\n"
                f"          'Please set it as an environment variable before starting the application.'\n"
                f"      )"
            ),
            effort="Small",
        ))
        fid += 1
    return results


# ── Scorecard builder ─────────────────────────────────────────

def _build_scorecard(findings: list[Finding], stats: dict) -> Scorecard:
    """Derive dimension scores from findings and basic stats."""

    def _penalty(category: str, sev_weights: dict[str, float]) -> float:
        total = 0.0
        for f in findings:
            if f.category == category:
                total += sev_weights.get(f.severity, 0)
        return min(total, 8.0)   # cap deduction at 8

    W = {"Critical": 3.0, "High": 1.5, "Medium": 0.6, "Low": 0.2, "Informational": 0.0}

    security        = max(0.5, 10.0 - _penalty("Security", W))
    maintainability = max(1.0, 10.0 - _penalty("Maintainability", W))
    reliability     = max(1.0, 10.0 - _penalty("Reliability", W))
    documentation   = max(1.0, 10.0 - _penalty("Documentation", W))
    testability     = 3.0 if any(f.id == "TEST-001" for f in findings) else 7.0
    observability   = max(2.0, 10.0 - _penalty("Observability", W))
    config_score    = max(2.0, 10.0 - _penalty("Configuration", W))

    # Boost scores for repos with more files / languages (maturity proxy)
    file_bonus = min(1.0, stats["file_count"] / 20)
    base = 6.0 + file_bonus

    return Scorecard(
        architecture=base,
        design=base,
        coding_standards=max(1.0, maintainability - 0.5),
        maintainability=maintainability,
        readability=max(2.0, documentation - 0.5),
        performance=base,
        security=security,
        scalability=base - 0.5,
        reliability=reliability,
        testability=testability,
        documentation=documentation,
        devops_readiness=base - 1.0,
        cloud_readiness=base - 1.0,
        api_design=base,
        database_design=base,
        automation_practices=testability - 0.5,
        enterprise_compliance=max(1.0, security - 1.0),
        production_readiness=max(1.0, (security + reliability + testability) / 3),
        enterprise_readiness=max(1.0, (security + maintainability + testability) / 3),
    )


# ── Verdict logic ─────────────────────────────────────────────

def _derive_verdict(scorecard: Scorecard, findings: list[Finding]) -> tuple[FinalVerdict, str, bool, bool]:
    score = scorecard.overall()
    critical = sum(1 for f in findings if f.severity == "Critical")
    high     = sum(1 for f in findings if f.severity == "High")

    if critical == 0 and high == 0 and score >= 8.0:
        return "Enterprise Ready", "No critical or high findings and overall score is strong.", True, True
    if critical == 0 and score >= 7.0:
        return "Production Ready", "No critical findings; minor improvements recommended.", True, False
    if critical == 0 and score >= 5.5:
        return "Production Ready with Minor Improvements", f"{high} high-severity finding(s) should be addressed.", True, False
    if critical <= 1 and score >= 4.5:
        return "Production Ready with Moderate Improvements", f"{critical} critical and {high} high findings require attention.", False, False
    if score >= 3.0:
        return "Requires Refactoring Before Production", f"{critical} critical finding(s) must be resolved before deploying.", False, False
    return "Requires Major Refactoring", "Significant security and quality issues found across the codebase.", False, False


# ── Roadmap builder ───────────────────────────────────────────

def _build_roadmap(findings: list[Finding]) -> list[RoadmapItem]:
    roadmap: list[RoadmapItem] = []

    critical_ids = [f.id for f in findings if f.severity == "Critical"]
    if critical_ids:
        roadmap.append(RoadmapItem(
            title="Resolve all critical security findings",
            priority="Immediate",
            effort="Medium",
            finding_ids=critical_ids,
            expected_benefit="Eliminates immediate security risk and potential data breach exposure.",
        ))

    high_ids = [f.id for f in findings if f.severity == "High"]
    if high_ids:
        roadmap.append(RoadmapItem(
            title="Address high-severity findings",
            priority="Short Term",
            effort="Medium",
            finding_ids=high_ids,
            expected_benefit="Improves stability and reduces operational risk.",
        ))

    test_ids = [f.id for f in findings if f.category == "Testability"]
    if test_ids:
        roadmap.append(RoadmapItem(
            title="Establish automated test suite",
            priority="Short Term",
            effort="Large",
            finding_ids=test_ids,
            expected_benefit="Enables safe refactoring and catches regressions automatically.",
        ))

    debt_ids = [f.id for f in findings if f.category == "Maintainability"]
    if debt_ids:
        roadmap.append(RoadmapItem(
            title="Clear technical debt (TODO/FIXME items)",
            priority="Medium Term",
            effort="Medium",
            finding_ids=debt_ids,
            expected_benefit="Reduces long-term maintenance burden and cognitive overhead.",
        ))

    doc_ids = [f.id for f in findings if f.category == "Documentation"]
    if doc_ids:
        roadmap.append(RoadmapItem(
            title="Improve code documentation",
            priority="Long Term",
            effort="Small",
            finding_ids=doc_ids[:10],
            expected_benefit="Accelerates onboarding and reduces knowledge silos.",
        ))

    return roadmap


# ── Main entry point ──────────────────────────────────────────

def _run_static_review(
    files: list[SourceFile],
    repo_name: str,
    repo_source: str,
    progress_cb: Callable[[str], None] | None = None,
) -> ReviewReport:
    """Run the built-in static-analysis pipeline (no LLM required)."""
    def _prog(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    _prog("Collecting repository statistics…")
    stats = collect_stats(files)
    logger.info(
        "Review started: repo=%r files=%d loc=%d languages=%s",
        repo_name, stats["file_count"], stats["estimated_loc"],
        ",".join(stats["languages"]),
    )

    _prog(f"Analysing {stats['file_count']} files for security and quality issues…")

    findings: list[Finding] = []
    findings += _findings_secrets(files)
    findings += _findings_sql_injection(files)
    _prog("Checking error handling and observability…")
    findings += _findings_bare_except(files)
    findings += _findings_print_debug(files)
    findings += _findings_env_not_validated(files)
    _prog("Checking maintainability and technical debt…")
    findings += _findings_todos(files)
    findings += _findings_long_functions(files)
    _prog("Checking documentation and test coverage…")
    findings += _findings_missing_tests(files)
    findings += _findings_missing_docstrings(files)

    _prog("Scoring codebase and building report…")
    scorecard = _build_scorecard(findings, stats)
    verdict, justification, prod_ready, ent_ready = _derive_verdict(scorecard, findings)
    roadmap = _build_roadmap(findings)

    critical = sum(1 for f in findings if f.severity == "Critical")
    high     = sum(1 for f in findings if f.severity == "High")

    strengths: list[str] = []
    if not any(f.id.startswith("SEC") for f in findings):
        strengths.append("No hardcoded secrets or obvious injection vulnerabilities detected.")
    if stats["file_count"] > 5:
        strengths.append(f"Codebase spans {stats['file_count']} files across {len(stats['languages'])} language(s).")
    if stats["frameworks"]:
        strengths.append(f"Uses established frameworks: {', '.join(stats['frameworks'])}.")
    if not any(f.id == "TEST-001" for f in findings):
        strengths.append("Test suite is present.")
    if not strengths:
        strengths.append("Codebase was successfully parsed and analysed.")

    weaknesses: list[str] = []
    if critical:
        weaknesses.append(f"{critical} critical security finding(s) must be resolved before any deployment.")
    if high:
        weaknesses.append(f"{high} high-severity finding(s) require attention.")
    if any(f.id == "TEST-001" for f in findings):
        weaknesses.append("No automated tests found — high regression risk.")
    if any(f.category == "Maintainability" for f in findings):
        weaknesses.append("Unresolved technical debt (TODO/FIXME comments or overly long functions).")

    missing_practices: list[str] = []
    if any(f.id == "TEST-001" for f in findings):
        missing_practices.append("Automated testing (unit / integration tests)")
    if any(f.category == "Documentation" for f in findings):
        missing_practices.append("Docstrings on public API symbols")
    if any(f.category == "Observability" for f in findings):
        missing_practices.append("Structured logging (replace print() with logging module)")

    report = ReviewReport(
        repo_name=repo_name,
        repo_source=repo_source,
        languages=stats["languages"],
        frameworks=stats["frameworks"],
        file_count=stats["file_count"],
        estimated_loc=stats["estimated_loc"],
        project_type=", ".join(stats["languages"][:2]) + " project" if stats["languages"] else "Unknown",
        overall_score=scorecard.overall(),
        confidence_pct=80,
        strengths=strengths,
        weaknesses=weaknesses,
        production_ready=prod_ready,
        enterprise_ready=ent_ready,
        final_verdict=verdict,
        verdict_justification=justification,
        architecture_review=(
            f"Static analysis completed across {stats['file_count']} file(s) "
            f"({stats['estimated_loc']} estimated lines of code). "
            f"Detected languages: {', '.join(stats['languages']) or 'unknown'}. "
            f"Frameworks in use: {', '.join(stats['frameworks']) or 'none detected'}. "
            f"Found {len(findings)} total finding(s): "
            f"{critical} critical, {high} high, "
            f"{sum(1 for f in findings if f.severity == 'Medium')} medium, "
            f"{sum(1 for f in findings if f.severity == 'Low')} low."
        ),
        findings=findings,
        technical_debt=(
            f"{sum(1 for f in findings if f.category == 'Maintainability')} maintainability finding(s) identified. "
            "Resolve TODO/FIXME comments and refactor long functions to reduce long-term maintenance cost."
            if any(f.category == "Maintainability" for f in findings)
            else "No significant technical debt markers detected."
        ),
        missing_practices=missing_practices,
        scorecard=scorecard,
        roadmap=roadmap,
    )

    logger.info(
        "Static analysis complete: repo=%r findings=%d overall=%.1f verdict=%r",
        repo_name, len(report.findings), report.overall_score, report.final_verdict,
    )
    return report


def run_review(
    files: list[SourceFile],
    repo_name: str,
    repo_source: str,
    progress_cb: Callable[[str], None] | None = None,
) -> ReviewReport:
    """
    Run a code review using the best available engine:
      1. Groq    — free cloud LLM, needs GROQ_API_KEY in .env
      2. Static  — built-in pattern checks, always works
    """
    # 1 — Try Groq (free cloud)
    try:
        from groq_reviewer import run_groq_review
        report = run_groq_review(files, repo_name, repo_source, progress_cb)
        if report is not None:
            return report
    except Exception as exc:
        logger.warning("Groq reviewer error: %s", exc)

    # 2 — Fall back to static analysis
    if progress_cb:
        progress_cb("Running built-in static analysis…")
    return _run_static_review(files, repo_name, repo_source, progress_cb)
