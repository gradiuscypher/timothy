import { Link } from "@tanstack/react-router";

import { useMyGuilds } from "@/api/hooks";
import {
  Badge,
  Card,
  Cell,
  Empty,
  ErrorNote,
  GuildName,
  Loading,
  PageTitle,
  Row,
  Table,
  When,
} from "@/components/ui";

/**
 * The servers this person administers that Timothy is in.
 *
 * Named where Timothy knows the name. It is not asked of Discord to draw this — a call
 * per row would spend the rate-limit budget enforcement runs on — but stored when the
 * gateway mentions the guild, which it does on every reconnect. A server Timothy has not
 * seen since the names were stored shows its ID alone, and the page still works.
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
                    <GuildName id={guild.guild_id} name={guild.name} />
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
