# Orders

Work in HAI is an **order**: a spec that a factory fulfils, the way a factory fills a product order. The model is
The lab owner's: *"I'm thinking of them as product orders that a factory fulfills, makes according to the specifications and
desires, then ships and cleans up."*

| Stage | Who owns it | What it answers |
|---|---|---|
| **Order** | the spec | what to make, and how the person wants to be told |
| **Fulfilment** | the factory | runs its standard line: claim the card, check it is healthy, run, watch the steps, honour a stop |
| **Shipping** | the order's declared deliverables | what actually leaves the building |
| **Cleanup** | retirement | the line is cleared for the next order |

## Product and byproduct

An order says which of its outputs is the **product**. A training order ships weights; its preview images are
byproducts. A benchmark order ships a number; its weights are byproducts. Byproducts that nobody declared end up
sitting somewhere shared, so declaring them is part of the order.

An order whose deliverable lives outside the order is not self-contained: if the thing that held the result dies, the
result dies with it.

## A measurement is an order too

Anything with inputs, a method, deliverables and cleanup is an order, including a measurement. Writing it as an order
rather than a script means it inherits the factory's standard line for free: the card checks, the resume, the
delivery retries and the cleanup. The next measurement is then a spec file, not a code change.

## What happens when an order cannot run

There are three fates, and only one of them is a property of the order itself:

- **Wait.** The lane is busy. The order stays queued and runs when the lane is free.
- **Archive.** The spec is broken. Only a defect in the order consumes the order.
- **Hold for a person.** The machinery misbehaved (a worker that will not stop, a card that will not come back). The
  order stays queued, untouched, and the factory stops taking new work until a person looks.

A busy lane or a misbehaving machine never throws an order away.

## Who places an order

A person can hand an order to one factory directly. Orders sent to the fleet go to the head, which weighs the
factories that can run it (what each one can do, what it is doing now, what it costs there) and passes the order to
that factory's own queue. The head never runs a factory's work itself.
