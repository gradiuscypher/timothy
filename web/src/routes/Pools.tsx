import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { useCreatePool, usePools, useSignedIn } from "@/api/hooks";
import {
  Button,
  Card,
  CardTitle,
  Cell,
  Empty,
  ErrorNote,
  Field,
  Input,
  Loading,
  PageTitle,
  Row,
  Table,
  When,
} from "@/components/ui";

/** Every pool, and — for pool managers — the form that makes another one. */
export function Pools() {
  const pools = usePools();
  const create = useCreatePool();
  const session = useSignedIn();
  const canManage = session.data?.manages_pools ?? false;
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  return (
    <>
      <PageTitle>Pools</PageTitle>

      <div className={canManage ? "grid gap-4 lg:grid-cols-[2fr_1fr]" : undefined}>
        <Card>
          <CardTitle>All pools</CardTitle>
          {pools.isPending ? <Loading what="pools" /> : null}
          <ErrorNote error={pools.error} />
          {pools.data?.length === 0 ? <Empty>No pools yet.</Empty> : null}
          {pools.data?.length ? (
            <Table head={["Name", "Description", "Created"]}>
              {pools.data.map((pool) => (
                <Row key={pool.id}>
                  <Cell>
                    <Link
                      to="/pools/$name"
                      params={{ name: pool.name }}
                      className="font-medium text-accent hover:underline"
                    >
                      {pool.name}
                    </Link>
                  </Cell>
                  <Cell className="text-surface-muted">{pool.description ?? "—"}</Cell>
                  <Cell>
                    <When iso={pool.created_at} />
                  </Cell>
                </Row>
              ))}
            </Table>
          ) : null}
        </Card>

        {canManage ? (
          <Card>
            <CardTitle>New pool</CardTitle>
            <form
              className="space-y-3"
              onSubmit={(event) => {
                event.preventDefault();
                create.mutate(
                  { name, description: description || null },
                  {
                    onSuccess: () => {
                      setName("");
                      setDescription("");
                    },
                  },
                );
              }}
            >
              <Field label="Name" hint="What moderators will type in /add_ban.">
                <Input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  maxLength={64}
                  required
                  placeholder="raiders"
                />
              </Field>
              <Field label="Description">
                <Input
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="Coordinated raid accounts"
                />
              </Field>
              <ErrorNote error={create.error} />
              <Button type="submit" variant="primary" disabled={create.isPending}>
                {create.isPending ? "Creating…" : "Create pool"}
              </Button>
              <p className="text-xs text-surface-muted">
                A new pool enforces nothing until a server subscribes to it.
              </p>
            </form>
          </Card>
        ) : null}
      </div>
    </>
  );
}
