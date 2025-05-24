"""
Task validation system for pre-execution checks and policy-based validation.
"""
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from enum import Enum

# Configure logging for validation messages
logger = logging.getLogger(__name__)

class ValidationSeverity(Enum):
    """Severity levels for validation issues."""
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"

# PreExecutionValidationError is now in exceptions.py

class ValidationPolicy:
    """Policy-based validation system."""
    
    def __init__(self):
        self.rules = []
    
    def add_rule(self, rule_func: Callable, severity: ValidationSeverity = ValidationSeverity.ERROR, description: str = ""):
        """Add validation rule with severity."""
        self.rules.append({
            'func': rule_func,
            'severity': severity,
            'description': description
        })
    
    def validate(self, target) -> Tuple[bool, List[Dict]]:
        """Run all validation rules and return results."""
        issues = []
        has_errors = False
        
        for rule in self.rules:
            try:
                rule['func'](target)
            except Exception as e:
                issue = {
                    'severity': rule['severity'],
                    'description': rule['description'],
                    'error': str(e),
                    'rule_func': rule['func'].__name__
                }
                issues.append(issue)
                
                if rule['severity'] == ValidationSeverity.ERROR:
                    has_errors = True
        
        return not has_errors, issues

class ResourceValidator:
    """Validates resource requirements before execution."""
    
    def __init__(self, max_memory_mb: int = 1024, max_cpu_percent: int = 80):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_percent = max_cpu_percent
    
    def validate_system_resources(self, task) -> bool:
        """Check if system has enough resources."""
        try:
            import psutil
            
            # Check memory
            memory = psutil.virtual_memory()
            if memory.percent > 90:
                raise Exception(f"System memory usage too high: {memory.percent}%")
            
            # Check CPU
            cpu_percent = psutil.cpu_percent(interval=0.1)
            if cpu_percent > self.max_cpu_percent:
                raise Exception(f"System CPU usage too high: {cpu_percent}%")
            
            # Check if task has resource requirements
            if hasattr(task, 'memory_requirement_mb') and task.memory_requirement_mb is not None:
                available_memory = (memory.total - memory.used) / 1024 / 1024
                if task.memory_requirement_mb > available_memory:
                    raise Exception(
                        f"Task requires {task.memory_requirement_mb}MB but only "
                        f"{available_memory:.0f}MB available"
                    )
            
            return True
            
        except ImportError:
            # psutil not available, skip resource checking
            logger.warning("psutil not available, skipping resource validation")
            return True
        except Exception as e:
            raise Exception(f"Resource validation failed: {e}")

class DependencyValidator:
    """Validates task dependencies."""
    
    def __init__(self):
        self.dependency_graph = {}
    
    def add_dependency(self, task_id: str, depends_on: List[str]):
        """Add dependency relationship."""
        self.dependency_graph[task_id] = depends_on
    
    def validate_dependencies(self, tasks: List) -> bool:
        """Validate all dependencies are satisfied."""
        task_ids = {task.id for task in tasks}
        
        for task in tasks:
            if task.id in self.dependency_graph:
                dependencies = self.dependency_graph[task.id]
                missing_deps = set(dependencies) - task_ids
                
                if missing_deps:
                    raise Exception(
                        f"Task {task.id} has missing dependencies: {missing_deps}"
                    )
        
        # Check for circular dependencies
        if self._has_circular_dependencies(task_ids):
            raise Exception("Circular dependencies detected")
        
        return True
    
    def _has_circular_dependencies(self, task_ids: set) -> bool:
        """Detect circular dependencies using DFS."""
        visited = set()
        rec_stack = set()
        
        def dfs(task_id):
            if task_id in rec_stack:
                return True  # Circular dependency found
            if task_id in visited:
                return False
            
            visited.add(task_id)
            rec_stack.add(task_id)
            
            for dep in self.dependency_graph.get(task_id, []):
                if dfs(dep):
                    return True
            
            rec_stack.remove(task_id)
            return False
        
        for task_id in task_ids:
            if task_id not in visited:
                if dfs(task_id):
                    return True
        
        return False
    
    def get_execution_order(self, tasks: List) -> List:
        """Get topologically sorted execution order."""
        task_map = {task.id: task for task in tasks}
        ordered = []
        processed = set()
        
        def process_task(task_id):
            if task_id in processed:
                return
            
            # Process dependencies first
            for dep_id in self.dependency_graph.get(task_id, []):
                if dep_id in task_map:
                    process_task(dep_id)
            
            if task_id in task_map:
                ordered.append(task_map[task_id])
                processed.add(task_id)
        
        for task in tasks:
            process_task(task.id)
        
        return ordered
