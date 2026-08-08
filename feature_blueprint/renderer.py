from __future__ import annotations

from feature_blueprint.models import RequirementsDocument, Traceable


def render_markdown(document: RequirementsDocument) -> str:
    lines: list[str] = [
        f"# {document.project.name} Requirements",
        "",
        f"- Target: {document.project.target_url}",
        f"- Scope: {document.project.scope}",
        f"- Generated: {document.project.generated_at}",
        f"- Model: {document.generation.synthesis_model} ({document.generation.profile.value})",
        "",
        "## Frontend",
        "",
    ]
    for page in document.frontend.pages:
        route = f" (`{page.route}`)" if page.route else ""
        lines.extend(
            [
                f"### {page.name}{route}",
                "",
                page.purpose,
                "",
                _trace_line(page),
                "",
            ]
        )
        if page.layout:
            lines.extend(["Layout: " + ", ".join(page.layout), ""])
        if page.states:
            lines.extend(["States: " + ", ".join(page.states), ""])
        if page.components:
            lines.extend(["Components:", ""])
            for component in page.components:
                lines.append(f"- **{component.name}** ({component.type}): {component.purpose}")
                if component.data_fields:
                    lines.append(f"  Data: {', '.join(component.data_fields)}")
                if component.interactions:
                    lines.append(f"  Interactions: {', '.join(component.interactions)}")
                if component.states:
                    lines.append(f"  States: {', '.join(component.states)}")
                lines.append(f"  {_trace_line(component)}")
            lines.append("")

    lines.extend(["### Workflows", ""])
    for workflow in document.frontend.workflows:
        lines.extend([f"#### {workflow.name}", "", f"Actor: {workflow.actor}", ""])
        if workflow.preconditions:
            lines.extend(["Preconditions:", "", *[f"- {item}" for item in workflow.preconditions], ""])
        lines.extend(["Steps:", "", *[f"{index}. {step}" for index, step in enumerate(workflow.steps, 1)], ""])
        if workflow.outcomes:
            lines.extend(["Outcomes:", "", *[f"- {item}" for item in workflow.outcomes], ""])
        lines.extend([_trace_line(workflow), ""])

    lines.extend(["## Backend", "", "### API endpoints", ""])
    for endpoint in document.backend.api_endpoints:
        lines.extend(
            [
                f"#### `{endpoint.method} {endpoint.path}`",
                "",
                endpoint.purpose,
                "",
                f"Authentication: {endpoint.authentication}",
                "",
            ]
        )
        if endpoint.parameters:
            lines.extend(["Parameters:", ""])
            for parameter in endpoint.parameters:
                required = "required" if parameter.required else "optional"
                lines.append(
                    f"- `{parameter.name}` ({parameter.location}, {parameter.type}, {required}): "
                    f"{parameter.description}"
                )
            lines.append("")
        if endpoint.request_body:
            lines.extend([f"Request body: {endpoint.request_body}", ""])
        lines.extend([f"Response: {endpoint.response_shape}", ""])
        if endpoint.errors:
            lines.extend(["Errors:", "", *[f"- {item}" for item in endpoint.errors], ""])
        if endpoint.business_rules:
            lines.extend(
                ["Endpoint rules:", "", *[f"- {item}" for item in endpoint.business_rules], ""]
            )
        lines.extend([_trace_line(endpoint), ""])

    lines.extend(["### Business rules", ""])
    for rule in document.backend.business_rules:
        lines.extend([f"- **{rule.category}:** {rule.description} — {_trace_line(rule)}"])
    lines.extend(["", "## Database", ""])
    for entity in document.database.entities:
        lines.extend([f"### {entity.name}", "", entity.purpose, "", _trace_line(entity), ""])
        lines.extend(["| Field | Type | Required | Unique | Description |", "|---|---|---:|---:|---|"])
        for field in entity.fields:
            lines.append(
                f"| {field.name} | {field.type} | {'yes' if field.required else 'no'} | "
                f"{'yes' if field.unique else 'no'} | {field.description} |"
            )
        lines.append("")

    if document.database.relationships:
        lines.extend(["### Relationships", ""])
        for relationship in document.database.relationships:
            lines.append(
                f"- {relationship.description} ({relationship.cardinality}) — {_trace_line(relationship)}"
            )
        lines.append("")
    if document.database.indexes:
        lines.extend(["### Suggested indexes", ""])
        for index in document.database.indexes:
            unique = "unique " if index.unique else ""
            lines.append(
                f"- {index.entity}: {unique}index on {', '.join(index.fields)} — {index.reason}; "
                f"{_trace_line(index)}"
            )
        lines.append("")

    lines.extend(["## Assumptions", ""])
    if document.assumptions:
        for assumption in document.assumptions:
            evidence = ", ".join(assumption.evidence_ids) or "none"
            lines.append(
                f"- {assumption.description} — {assumption.reason} "
                f"(confidence: {assumption.confidence.value}; evidence: {evidence})"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Unknowns", ""])
    lines.extend([f"- {item}" for item in document.unknowns] or ["- None"])
    if document.generation.warnings:
        lines.extend(["", "## Generation warnings", ""])
        lines.extend(f"- {warning}" for warning in document.generation.warnings)
    return "\n".join(lines).rstrip() + "\n"


def _trace_line(item: Traceable) -> str:
    evidence = ", ".join(item.evidence_ids) or "none"
    return f"Basis: {item.basis.value}; confidence: {item.confidence.value}; evidence: {evidence}"

