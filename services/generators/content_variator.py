"""Template-based content variation engine.

Takes content templates with variables and generates thousands of
unique document contents through systematic substitution.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from random import Random
from typing import Any, Dict, List, Optional

try:
    from jinja2 import Environment, BaseLoader, TemplateSyntaxError
    _HAS_JINJA2 = True
except ImportError:
    _HAS_JINJA2 = False

logger = logging.getLogger(__name__)


class ContentVariator:
    """Generates content variations through template substitution.
    
    Supports two template syntaxes:
    1. Simple: {variable} placeholders
    2. Jinja2: {{ variable }}, {% for %}, etc. (if jinja2 installed)
    
    Args:
        seed: Seed for reproducible randomness.
        timeline_days: Days to spread date variations across.
    
    Example:
        >>> variator = ContentVariator(seed=42)
        >>> contents = variator.expand_template(
        ...     template="Meeting notes for {project}\\nDate: {date}",
        ...     variables={"project": ["Alpha", "Beta"]},
        ...     target_count=10
        ... )
    """
    
    _SIMPLE_VAR_PATTERN = re.compile(r"\{(\w+)\}")
    
    def __init__(
        self,
        seed: int = 42,
        timeline_days: int = 90,
    ) -> None:
        self._rng = Random(seed)
        self._timeline_days = timeline_days
        self._base_date = datetime.now()
        
        if _HAS_JINJA2:
            self._jinja_env = Environment(loader=BaseLoader())
    
    def expand_template(
        self,
        template: str,
        variables: Dict[str, List[str]],
        target_count: int,
        use_jinja: bool = False,
    ) -> List[str]:
        """Expand a template into multiple content variations.
        
        Args:
            template: Content template with variable placeholders.
            variables: Dict of variable name → possible values.
            target_count: Number of variations to generate.
            use_jinja: Use Jinja2 templating (requires jinja2 package).
        
        Returns:
            List of content strings.
        """
        if use_jinja and _HAS_JINJA2:
            return self._expand_jinja(template, variables, target_count)
        return self._expand_simple(template, variables, target_count)
    
    def _expand_simple(
        self,
        template: str,
        variables: Dict[str, List[str]],
        target_count: int,
    ) -> List[str]:
        """Expand using simple {variable} syntax."""
        # Find all variables in template
        var_names = self._SIMPLE_VAR_PATTERN.findall(template)
        
        if not var_names:
            return [template] * min(target_count, 1)
        
        # Add automatic variables
        auto_vars = self._generate_auto_variables(var_names)
        all_vars = {**auto_vars, **variables}
        
        contents = []
        seen = set()
        attempts = 0
        max_attempts = target_count * 5
        
        while len(contents) < target_count and attempts < max_attempts:
            # Generate a random combination
            substitutions = {}
            for var_name in var_names:
                if var_name in all_vars and all_vars[var_name]:
                    substitutions[var_name] = self._rng.choice(all_vars[var_name])
                else:
                    substitutions[var_name] = f"[{var_name}]"
            
            # Create content key for deduplication
            content_key = tuple(sorted(substitutions.items()))
            
            if content_key not in seen:
                seen.add(content_key)
                
                # Apply substitutions
                content = template
                for var_name, value in substitutions.items():
                    content = content.replace(f"{{{var_name}}}", value)
                
                contents.append(content)
            
            attempts += 1
        
        return contents
    
    def _expand_jinja(
        self,
        template: str,
        variables: Dict[str, List[str]],
        target_count: int,
    ) -> List[str]:
        """Expand using Jinja2 templating."""
        if not _HAS_JINJA2:
            logger.warning("Jinja2 not available, falling back to simple expansion")
            return self._expand_simple(template, variables, target_count)
        
        try:
            jinja_template = self._jinja_env.from_string(template)
        except TemplateSyntaxError as e:
            logger.error("Jinja2 template syntax error: %s", e)
            return self._expand_simple(template, variables, target_count)
        
        # Find referenced variables
        var_names = set()
        for match in re.finditer(r"\{\{\s*(\w+)\s*\}\}", template):
            var_names.add(match.group(1))
        
        # Add automatic variables
        auto_vars = self._generate_auto_variables(var_names)
        all_vars = {**auto_vars, **variables}
        
        contents = []
        seen = set()
        attempts = 0
        max_attempts = target_count * 5
        
        while len(contents) < target_count and attempts < max_attempts:
            # Generate random context
            context = {}
            for var_name in var_names:
                if var_name in all_vars and all_vars[var_name]:
                    context[var_name] = self._rng.choice(all_vars[var_name])
                else:
                    context[var_name] = f"[{var_name}]"
            
            content_key = tuple(sorted(context.items()))
            
            if content_key not in seen:
                seen.add(content_key)
                try:
                    content = jinja_template.render(**context)
                    contents.append(content)
                except Exception as e:
                    logger.warning("Jinja2 render error: %s", e)
            
            attempts += 1
        
        return contents
    
    def _generate_auto_variables(
        self,
        var_names: set[str] | List[str],
    ) -> Dict[str, List[str]]:
        """Generate automatic variables (dates, times, etc.)."""
        auto_vars: Dict[str, List[str]] = {}
        
        if "date" in var_names:
            auto_vars["date"] = self._generate_dates(30, ["%B %d, %Y", "%Y-%m-%d"])
        
        if "time" in var_names:
            auto_vars["time"] = self._generate_times(20)
        
        if "year" in var_names:
            auto_vars["year"] = ["2023", "2024"]
        
        if "quarter" in var_names:
            auto_vars["quarter"] = ["Q1", "Q2", "Q3", "Q4"]
        
        if "month" in var_names:
            auto_vars["month"] = [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"
            ]
        
        if "weekday" in var_names:
            auto_vars["weekday"] = [
                "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
            ]
        
        return auto_vars
    
    def _generate_dates(
        self,
        count: int,
        formats: List[str],
    ) -> List[str]:
        """Generate date strings."""
        dates = []
        for _ in range(count):
            days_ago = self._rng.randint(0, self._timeline_days)
            date = self._base_date - timedelta(days=days_ago)
            fmt = self._rng.choice(formats)
            dates.append(date.strftime(fmt))
        return list(set(dates))
    
    def _generate_times(self, count: int) -> List[str]:
        """Generate time strings."""
        times = []
        for _ in range(count):
            hour = self._rng.randint(8, 18)
            minute = self._rng.choice([0, 15, 30, 45])
            times.append(f"{hour:02d}:{minute:02d}")
        return list(set(times))
    
    def generate_document_content(
        self,
        content_theme: str,
        document_type: str,
        variables: Dict[str, List[str]],
        target_length: int = 500,
    ) -> str:
        """Generate realistic document content based on theme.
        
        Args:
            content_theme: Description of what the document should contain.
            document_type: File type (docx, txt, md, etc.).
            variables: Variables for substitution.
            target_length: Approximate target length in characters.
        
        Returns:
            Generated document content.
        """
        # Select appropriate template based on type and theme
        template = self._select_template(content_theme, document_type)
        
        # Expand once
        contents = self.expand_template(
            template=template,
            variables=variables,
            target_count=1,
        )
        
        return contents[0] if contents else ""
    
    def _select_template(self, theme: str, doc_type: str) -> str:
        """Select an appropriate template based on theme and type."""
        theme_lower = theme.lower()
        
        if "meeting" in theme_lower or "notes" in theme_lower:
            return self._MEETING_TEMPLATE
        elif "report" in theme_lower or "status" in theme_lower:
            return self._REPORT_TEMPLATE
        elif "budget" in theme_lower or "financial" in theme_lower:
            return self._BUDGET_TEMPLATE
        elif "readme" in theme_lower or "documentation" in theme_lower:
            return self._README_TEMPLATE
        elif "shopping" in theme_lower or "list" in theme_lower:
            return self._LIST_TEMPLATE
        elif "recipe" in theme_lower:
            return self._RECIPE_TEMPLATE
        else:
            return self._GENERIC_TEMPLATE
    
    # Content templates
    _MEETING_TEMPLATE = """Meeting Notes - {date}
========================

Attendees: {attendees}
Subject: {subject}

Agenda:
1. Review previous action items
2. Project updates
3. Discussion topics
4. New action items

Notes:
{notes}

Action Items:
- {action1}
- {action2}
- {action3}

Next Meeting: {next_date}
"""
    
    _REPORT_TEMPLATE = """# {project} Status Report

**Date:** {date}
**Prepared by:** {author}
**Period:** {period}

## Executive Summary

{summary}

## Progress This Period

{progress}

## Key Metrics

- Completion: {completion}%
- Budget Status: {budget_status}
- Timeline: {timeline_status}

## Risks & Issues

{risks}

## Next Steps

{next_steps}

## Appendix

Additional details available upon request.
"""
    
    _BUDGET_TEMPLATE = """Budget Overview - {period} {year}
================================

Department: {department}
Prepared by: {author}
Date: {date}

SUMMARY
-------
Total Budget: ${budget_total}
Spent to Date: ${spent}
Remaining: ${remaining}

CATEGORIES
----------
Personnel: ${personnel}
Operations: ${operations}
Marketing: ${marketing}
Technology: ${technology}
Other: ${other}

NOTES
-----
{notes}
"""
    
    _README_TEMPLATE = """# {project}

{description}

## Installation

```bash
{install_command}
```

## Usage

{usage}

## Configuration

{config_info}

## Contributing

{contributing}

## License

{license}
"""
    
    _LIST_TEMPLATE = """{title}
{date}

Items:
{items}

Notes:
{notes}
"""
    
    _RECIPE_TEMPLATE = """# {dish_name}

Prep Time: {prep_time}
Cook Time: {cook_time}
Servings: {servings}

## Ingredients

{ingredients}

## Instructions

{instructions}

## Notes

{notes}
"""
    
    _GENERIC_TEMPLATE = """{title}

Date: {date}
Author: {author}

{content}

---
Last updated: {date}
"""


# Convenience function
def vary_content(
    template: str,
    variables: Dict[str, List[str]],
    count: int = 10,
    seed: int = 42,
) -> List[str]:
    """Convenience function to generate content variations.
    
    Args:
        template: Content template with {variable} placeholders.
        variables: Dict of variable name → possible values.
        count: Number of variations to generate.
        seed: Random seed.
    
    Returns:
        List of content variations.
    """
    variator = ContentVariator(seed=seed)
    return variator.expand_template(
        template=template,
        variables=variables,
        target_count=count,
    )
