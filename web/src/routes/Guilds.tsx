import { Link } from "@tanstack/react-router";

import { useMyGuilds } from "@/api/hooks";
import {
  Badge,
  Card,
  Cell,
  Empty,
  ErrorNote,
  Loading,
  PageTitle,
  Row,
  Snowflake,
  Table,
  When,
} from "@/components/ui";

/**
 * The servers this person administers that Timothy is in.
 *
 * Names are not shown, only IDs. Timothy's `guilds` table holds the ID and nothing else,
 * and fetching a hundred names from Discord to decorate a list would spend the same
 * rate-limit budget enforcement runs on. The server page is reached by ID either way.
 */
export function Guilds() {
  const guilds = useMyGuilds();

  return (
    <>
      <PageTitle>Your servers</PageTitle>
      <Card label="Your servers">
        {guilds.isPending ? <Loading what="your servers" /> : null}
        <ErrorNote error={guilds.error} />
        {guilds.data?.length === 0 ? (
          <Empty>
            You do not administer any server Timothy is in. Timothy only lists servers you
            were in when you signed in — if that has changed, sign out and back in.
          </Empty>
        ) : null}
        {guilds.data?.length ? (
          <Table head={["Server", "Timothy joined", "Enforcement"]}>
            {guilds.data.map((guild) => (
              <Row key={guild.guild_id}>
                <Cell>
                  <Link
                    to="/guilds/$guildId"
                    params={{ guildId: guild.guild_id }}
                    className="text-accent hover:underline"
                  >
                    <Snowflake id={guild.guild_id} />
                  </Link>
                </Cell>
                <Cell>
                  <When iso={guild.joined_at} />
                </Cell>
                <Cell>
                  {guild.enforcement_paused ? (
                    <Badge tone="warn">paused</Badge>
                  ) : (
                    <Badge tone="ok">active</Badge>
                  )}
                </Cell>
              </Row>
            ))}
          </Table>
        ) : null}
      </Card>
    </>
  );
}
