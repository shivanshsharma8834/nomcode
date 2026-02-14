from pydantic import BaseModel, Field

class CodeIssue(BaseModel):
    file_path: str = Field(description="The full path to the file containing the issue")
    line_number: int = Field(description="The line number where the issue occurs")
    issue_type: str = Field(description="Type of issue: 'Bug', 'Security', 'Performance', or 'Style'")
    suggestion: str = Field(description="Actionable advice to fix the issue")

class PRReview(BaseModel):
    summary: str = Field(description="A concise summary of the changes")
    issues: list[CodeIssue]
