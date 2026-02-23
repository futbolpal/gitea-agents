# Test Scenarios

## Scenario: Handling issue creation

Given an issue is created
When the kilo-agent sees an unreserved issue
Then the kilo-agent reserves the issue
And the kilo-agent spawns a subprocess to handle the work
Then the subprocess creates a new PR
Then the kilo-agent adds a label 'agent-in-review' indicating the work is complete.

## Scenario: Handling PR comment creation

Given kilo-agent has created a PR for an issue
When there is a new PR comment that has not been actioned on
Then the kilo-agent spawns a subprocess to handle the work
Then the subprocess updates the PR
Then the kilo-agent adds the 'heart' reaction to the comment

## Scenario: Handling Review comment creation

Given kilo-agent has created a PR for an issue
When there is a new Review comment that has not been actioned on
Then the kilo-agent spawns a subprocess to handle the work
Then the subprocess updates the PR
Then the kilo-agent adds the 'heart' reaction to the comment

## Scenario: Handling issue response failures

Given kilo-agent has reserved an issue
But the subprocess dies (non-0 exit code)
Then the kilo-agent will spawn a new subprocess to retry the work (up to 3 times)

## Scenario: Handling PR comment response failures

Given kilo-agent has reserved a PR comment
But the subprocess dies (non-0 exit code)
Then the kilo-agent will spawn a new subprocess to retry the work (up to 3 times)

## Scenario: Handling Review comment response failures

Given kilo-agent has reserved a Review comment
But the subprocess dies (non-0 exit code)
Then the kilo-agent will spawn a new subprocess to retry the work (up to 3 times)
