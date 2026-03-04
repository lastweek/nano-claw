# Nano-Claw Development Guidelines

## Testing Requirements

**All new features MUST include unit tests.**

### When adding a new feature:

1. **Write tests first** (TDD approach recommended):

   - Create test file in `tests/` directory
   - Write test cases for expected behavior
   - Include edge cases and error scenarios

2. **Test coverage**:

   - Aim for >80% code coverage
   - Test both success and failure paths
   - Test edge cases and boundary conditions

3. **Test naming**:

   - Use descriptive test names: `test_<function>_<scenario>`
   - Group tests in classes by feature

4. **Before committing**:

   ```bash
   # Run all tests
   pytest

   # Run with coverage
   pytest --cov=src --cov=tools --cov-report=html

   # Run specific test file
   pytest tests/test_tools.py
   ```

### Code Style

- Follow PEP 8 style guidelines
- Use type hints for function signatures
- Add docstrings to classes and public methods
- Keep functions focused and small

### Architecture Principles

- **Minimalism**: Keep it simple, avoid over-engineering
- **Reusability**: Extract common patterns to base classes/utilities
- **Clarity**: Clear names, good documentation, obvious intent

### Adding New Tools

When creating a new tool:

1. Inherit from `Tool` base class
2. Define `name`, `description`, and `parameters`
3. Implement `execute()` method returning `ToolResult`
4. Create comprehensive tests in `tests/test_<tool_name>_tool.py`
5. Register tool in `src/main.py`
6. Update README with tool description

### Examples

See existing tests for patterns:

- `tests/test_tools.py` - Base class testing
- `tests/test_read_tool.py` - Tool implementation testing
- `tests/test_context.py` - Dataclass testing
