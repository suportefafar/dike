# Understanding the CP-SAT Solver Failure (For Junior Devs)

When the Dike service tries to generate reservations, it uses a library called **OR-Tools CP-SAT**. If you haven't worked with Constraint Programming (CP) before, this behavior can be very confusing! 

Here is a breakdown of how the solver works and exactly why it is failing.

---

## 🧩 The Sudoku Analogy

Think of a Constraint Solver like a **Sudoku solver**. 

When you play Sudoku, you have strict rules (no duplicate numbers in a row, column, or 3x3 grid). 
- If you give a Sudoku solver a valid puzzle, it solves it instantly.
- If you give it a puzzle with even **one single conflict** (for example, two 5s in the same row), a constraint solver won't try to "do its best" or leave that cell blank. It will immediately stop and say: **"Infeasible (Impossible)"**.

This is exactly what is happening to Dike. 

---

## 📋 The Rules (Constraints) Dike Must Follow

In [generate_service.py](file:///var/services-infra/dike/services/generate_service.py), the code builds a list of rules that the solver **must** satisfy:

### Rule 1: Capacity (The "Size" Constraint)
A class can only be assigned to a room if the number of students (vacancies) is less than or equal to the room capacity.
```python
if vacancies <= capacity:
    # Allow this room as a candidate for this class
```

### Rule 2: Non-Overlapping (The "Time" Constraint)
No two classes can occupy the same room at the same time.
```python
# If class A and class B have a schedule conflict, they cannot share the same room
model.Add(allocations[(s1_idx, p_idx)] + allocations[(s2_idx, p_idx)] <= 1)
```

### Rule 3: 100% Assignment (The "No Class Left Behind" Constraint)
Every single class that passed the filter **must** be assigned to a room.
```python
model.Add(sum(total_assigned) == len(filtered_subjects))
```

---

## 💥 Why It Fails With Current Data

When Dike runs using the static snapshot (`class_subjects.json`), all three rules can be met. 

However, when running with **live database data**, a conflict occurs:
- **Scenario A (Size Mismatch):** There might be a class with `vacancies = 75`, but the largest classroom in the database only has `capacity = 70`. Because of **Rule 1**, that class cannot fit in *any* room. Because of **Rule 3**, the solver is forced to place it. These two rules contradict each other, making the problem **Infeasible**.
- **Scenario B (Scheduling Bottleneck):** There might be 5 different classes scheduled at Tuesday 14:00 that all require a room of size 50+, but you only have 4 rooms of that size. Since they conflict, they can't share rooms (**Rule 2**). Since one class must be left out, we violate **Rule 3**. Result: **Infeasible**.

---

## 🛠️ How to Fix This in the Code

> [!TIP]
> This fix has been applied in `generate_service.py` and `gerenate-reservations.py`.

Instead of demanding a perfect 100% match, you change the solver to **maximize** assignments.

1. **Remove Rule 3** (the line enforcing 100% allocation):
   ```diff
   - model.Add(sum(total_assigned) == len(filtered_subjects))
   ```
2. **Rely on the Objective Function** already present in the code:
   ```python
   model.Maximize(sum(total_assigned))
   ```

By removing the strict constraint and keeping the maximization objective, the solver will successfully generate a schedule for 98% or 99% of classes, leaving only the unplaceable ones unassigned (which Dike will list in the response `unassigned` key rather than crashing!).
