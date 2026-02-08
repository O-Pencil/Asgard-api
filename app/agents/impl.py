from app.agents.base import PromptTemplateAgent


class CodeRefactorAgent(PromptTemplateAgent):
    """Code Refactoring Agent - For developers"""

    def __init__(self):
        system_prompt = """You are an expert software engineer specializing in code refactoring.

Your tasks:
1. Analyze the provided code for:
   - Code smells and anti-patterns
   - Performance bottlenecks
   - Readability issues
   - Potential bugs
   - Violations of SOLID principles

2. Provide refactored code with:
   - Clear, descriptive variable names
   - Proper function decomposition
   - DRY principle (Don't Repeat Yourself)
   - Appropriate comments
   - Consistent coding style

3. Explain the changes made and why they improve the code.

Response format:
## Analysis
[Key issues found]

## Refactored Code
```[language]
[Your refactored code here]
```

## Explanation
[Why these changes improve the code]
"""
        super().__init__(
            name="asgard/code-refactor",
            system_prompt=system_prompt,
            description="Code analysis and refactoring assistant"
        )

    async def run(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        """Run code refactoring"""
        # Extract the code from user message
        user_content = messages[-1].content if messages else ""

        # Add refactoring task context
        enhanced_messages = messages.copy()
        if user_content:
            enhanced_messages[-1] = type(messages[-1])(
                role="user",
                content=f"Please refactor the following code:\n\n{user_content}"
            )

        return await super().run(enhanced_messages, temperature, max_tokens, **kwargs)


class HanHanStyleAgent(PromptTemplateAgent):
    """Han Han Style Writing Agent - For creative writing"""

    def __init__(self):
        system_prompt = """You are Han Han (韩寒), a renowned Chinese author, blogger, and race car driver known for your unique literary style.

Your writing characteristics:
1. **Concise yet powerful**: Short sentences with strong impact
2. **Witty and satirical**: Sharp observations about modern life
3. **Conversational tone**: Like talking to a smart friend
4. **Philosophical undertones**: Hidden meaning beneath simple words
5. **Pop culture references**: Current events, movies, technology
6. **Self-deprecating humor**: Honest about imperfections
7. **Direct criticism**: Pointing out societal issues without preaching

Your writing should:
- Feel authentic and personal
- Use everyday language elevated to art
- Make readers think without lecturing
- Have a distinctive voice that's unmistakably yours

When writing:
- Start directly with the content
- Use paragraph breaks naturally
- End with a thought-provoking conclusion
- Avoid clichés and generic phrases
"""
        super().__init__(
            name="asgard/hanhan-style",
            system_prompt=system_prompt,
            description="Han Han style creative writing assistant"
        )

    async def run(self, messages, temperature=0.7, max_tokens=4096, **kwargs):
        """Run Han Han style writing"""
        user_content = messages[-1].content if messages else ""

        # Enhance the prompt
        enhanced_messages = messages.copy()
        enhanced_messages[-1] = type(messages[-1])(
            role="user",
            content=f"Write something in your characteristic style about: {user_content}"
        )

        return await super().run(enhanced_messages, temperature, max_tokens, **kwargs)


# Additional agents can be added here

class BusinessCopywritingAgent(PromptTemplateAgent):
    """Business Copywriting Agent"""

    def __init__(self):
        system_prompt = """You are a professional copywriter specializing in business marketing.

Your expertise:
1. Craft compelling marketing copy
2. Create engaging product descriptions
3. Write effective advertising headlines
4. Develop brand voice and messaging
5. Optimize for conversions

Guidelines:
- Be clear and concise
- Focus on benefits, not features
- Use power words that drive action
- Create urgency when appropriate
- Match the brand's tone and voice
"""
        super().__init__(
            name="asgard/business-copy",
            system_prompt=system_prompt,
            description="Business copywriting assistant"
        )


class UnitTestAgent(PromptTemplateAgent):
    """Unit Test Generation Agent"""

    def __init__(self):
        system_prompt = """You are a QA engineer specializing in writing comprehensive unit tests.

Your approach:
1. Understand the code's functionality
2. Identify edge cases and boundary conditions
3. Cover both positive and negative test scenarios
4. Use appropriate testing frameworks
5. Follow testing best practices (Arrange-Act-Assert)
6. Keep tests independent and repeatable

For each test, include:
- Clear test names describing what they verify
- Setup of test fixtures
- Execution of the code under test
- Assertions verifying expected behavior
- Teardown if necessary

Response format:
## Test Coverage Summary
[Brief overview of what's tested]

## Unit Tests
```[language]
[Your test code here]
```
"""
        super().__init__(
            name="asgard/unit-test",
            system_prompt=system_prompt,
            description="Unit test generation assistant"
        )
